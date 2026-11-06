"""Publish vector evidence, extracted content and completion under one job fence."""

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import delete, select, update

from src.models.document import Document
from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence
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
        asset_ids = [block.provenance["asset_id"] for page in processed.extracted.pages for block in page.blocks if "asset_id" in block.provenance]
        await db.execute(update(DocumentDeletion).where(
            DocumentDeletion.kind == "asset", DocumentDeletion.source_record_id == source.internal_id,
            DocumentDeletion.status == "PUBLISHED", DocumentDeletion.id.not_in(asset_ids),
        ).values(status="QUEUED", available_at=datetime.now(UTC)))
        if asset_ids:
            assets = (await db.execute(select(DocumentDeletion).where(
                DocumentDeletion.id.in_(asset_ids), DocumentDeletion.kind == "asset",
                DocumentDeletion.source_record_id == source.internal_id, DocumentDeletion.lease_token == lease.token,
                DocumentDeletion.status == "STAGED",
            ).with_for_update())).scalars().all()
            if len(assets) != len(asset_ids):
                raise RuntimeError("Derived source asset publication fence was lost")
            for asset in assets:
                asset.status, asset.lease_token = "PUBLISHED", None
        store = SupabaseVectorStore(session_factory=sessions)
        count = await store.index_document(
            source.document_id, processed.embedded, extra_payload=source.vector_payload(), session=db,
        )
        await db.execute(delete(DocumentEvidence).where(
            DocumentEvidence.document_kind == source.kind, DocumentEvidence.source_record_id == source.internal_id,
        ))
        structured = {}
        for page in processed.extracted.pages:
            for block in page.blocks:
                if not block.structured:
                    continue
                data = dict(block.structured)
                evidence_id = data["evidence_id"]
                if evidence_id in structured and data["kind"] == "table":
                    prior = structured[evidence_id]
                    prior["rows"] = prior["rows"] + data["rows"]
                    prior["row_pages"] = prior["row_pages"] + data["row_pages"]
                    prior["pages"] = prior["pages"] + data["pages"]
                    prior["regions"] = prior["regions"] + data["regions"]
                    prior["asset_ids"] = prior.get("asset_ids", []) + data.get("asset_ids", [])
                else:
                    structured[evidence_id] = {**data, "source_sha256": block.provenance.get("document_sha256", "")}
        for evidence_id, data in structured.items():
            db.add(DocumentEvidence(
                document_kind=source.kind, source_record_id=source.internal_id, evidence_id=evidence_id,
                client_id=source.client_id, tenant_id=source.tenant_id, collection_id=source.collection_id,
                subject_id=source.subject_id, source_sha256=data.pop("source_sha256", ""), kind=data["kind"], data_json=data,
            ))
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
