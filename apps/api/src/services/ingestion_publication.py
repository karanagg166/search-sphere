"""Publish vector evidence, extracted content and completion under one job fence."""

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import delete, select

from src.models.document import Document
from src.models.external_document import ExternalDocument
from src.models.external_document_content import ExternalDocumentContent
from src.models.medical_observation import MedicalObservation
from src.services.ingestion_source import load_ingestion_source
from src.vector_store.supabase_vector_store import SupabaseVectorStore


async def publish_ingestion(sessions, queue, lease, source, processed):
    async with sessions() as db, db.begin():
        job = await queue.lock_lease(db, lease)
        if job is None:
            return False
        current = await load_ingestion_source(db, source.kind, source.internal_id)
        if current != source:
            return False
        store = SupabaseVectorStore(session_factory=sessions)
        count = await store.index_document(
            source.document_id, processed.embedded, extra_payload=source.vector_payload(), session=db,
        )
        model = Document if source.kind == "standalone" else ExternalDocument
        row = await db.get(model, source.internal_id)
        row.chunk_count = count
        row.status = "indexed" if source.kind == "standalone" else "READY"
        if source.kind == "external":
            row.processing_error = None
            row.processed_at = datetime.now(UTC)
            text = processed.extracted.combined_text()
            content = (await db.execute(select(ExternalDocumentContent).where(
                ExternalDocumentContent.external_document_id == row.id,
            ))).scalar_one_or_none()
            if content is None:
                content = ExternalDocumentContent(external_document_id=row.id)
                db.add(content)
            content.raw_text, content.character_count = text, len(text)
            if source.client_id == "quick_clinic":
                await db.execute(delete(MedicalObservation).where(
                    MedicalObservation.source_system == source.client_id,
                    MedicalObservation.tenant_id == source.tenant_id,
                    MedicalObservation.external_document_id == source.document_id,
                    MedicalObservation.external_patient_id == source.subject_id,
                ))
                for observation in processed.observations:
                    db.add(MedicalObservation(
                        source_system=source.client_id, tenant_id=source.tenant_id,
                        external_patient_id=source.subject_id, external_document_id=source.document_id,
                        **asdict(observation),
                    ))
        job.status, job.stage, job.error_code = "SUCCEEDED", "complete", None
        job.lease_token = job.lease_expires_at = None
    return True
