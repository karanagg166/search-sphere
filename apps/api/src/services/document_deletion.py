"""Revoke searchable evidence and fence ingestion before private object cleanup."""

import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select

from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence
from src.models.external_document import ExternalDocument
from src.models.ingestion_job import IngestionJob
from src.models.medical_observation import MedicalObservation
from src.repositories.document_deletion import (
    chunk_scope,
    find_deletion,
    lock_document_identity,
)


async def delete_registered_document(db, kind, document_id, client_id, tenant_id, *, context=None):
    """Caller commits this transaction. No external request occurs under locks."""
    await lock_document_identity(db, kind, client_id, tenant_id, document_id)
    prior = await find_deletion(db, kind, client_id, tenant_id, document_id, context=context)
    if prior:
        return prior
    if kind == "standalone":
        query = select(Document).where(Document.id == document_id, Document.user_id == tenant_id)
    else:
        query = select(ExternalDocument).where(
            ExternalDocument.source_system == client_id, ExternalDocument.tenant_id == tenant_id,
            ExternalDocument.external_document_id == document_id,
        )
    document = (await db.execute(query)).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if context and kind == "external":
        context.resolve_scope(document.collection_id, document.owner_subject_id)
    # Publication, retries, and deletion all take the job lock before document writes.
    job = (await db.execute(select(IngestionJob).where(
        IngestionJob.document_kind == kind, IngestionJob.document_id == document.id,
    ).with_for_update())).scalar_one_or_none()
    document = (await db.execute(query.with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if job:
        job.status, job.stage, job.error_code = "FAILED", "deleted", "document_deleted"
        job.generation += 1
        job.lease_token = job.lease_expires_at = None
    row = DocumentDeletion(
        kind=kind, document_id=document_id, source_record_id=document.id,
        client_id=client_id, tenant_id=tenant_id,
        collection_id=None if kind == "standalone" else document.collection_id,
        subject_id=tenant_id if kind == "standalone" else document.owner_subject_id,
        storage_key=document.storage_key if kind == "standalone" else document.storage_path,
    )
    db.add(row)
    await db.flush()
    # pgvector and relational metadata share the same transaction. A failed delete
    # rolls everything back; a committed delete immediately hides all evidence.
    await db.execute(delete(DocumentChunk).where(*chunk_scope(row)))
    await db.execute(delete(DocumentEvidence).where(
        DocumentEvidence.document_kind == kind, DocumentEvidence.source_record_id == document.id,
    ))
    if kind == "external":
        await db.execute(delete(MedicalObservation).where(
            MedicalObservation.source_system == client_id, MedicalObservation.tenant_id == tenant_id,
            MedicalObservation.external_document_id == document_id,
        ))
    await db.delete(document)
    await db.flush()
    return row


async def delete_medical_object(db, context, storage_key):
    """Compatibility storage deletion also revokes any registered evidence."""
    from src.security.medical_context import bind_patient

    _, patient_id, document_id, _ = storage_key.split("/")
    context = bind_patient(context, patient_id)
    await lock_document_identity(db, "external", context.client_id, context.tenant_id, document_id)
    prior = await find_deletion(db, "external", context.client_id, context.tenant_id, document_id, context=context)
    if prior:
        if prior.storage_key != storage_key:
            raise HTTPException(status_code=409, detail="Storage path differs from the document cleanup target.")
        return prior
    registered = (await db.execute(select(ExternalDocument).where(
        ExternalDocument.source_system == context.client_id, ExternalDocument.tenant_id == context.tenant_id,
        ExternalDocument.external_document_id == document_id,
    ))).scalar_one_or_none()
    if registered:
        context.resolve_scope(registered.collection_id, registered.owner_subject_id)
        if registered.storage_path != storage_key:
            raise HTTPException(status_code=409, detail="Use the registered document storage path.")
        return await delete_registered_document(db, "external", document_id, context.client_id, context.tenant_id, context=context)
    row = DocumentDeletion(
        kind="external", document_id=document_id, source_record_id=str(uuid.uuid4()),
        client_id=context.client_id, tenant_id=context.tenant_id, collection_id=context.collection_id,
        subject_id=context.subject_id, storage_key=storage_key,
    )
    db.add(row)
    await db.flush()
    return row
