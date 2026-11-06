"""Atomic external identity registration and PostgreSQL ingestion outbox."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select

from src.models.external_document import ExternalDocument
from src.repositories.document_deletion import (
    lock_document_identity,
    reject_deleted_identity,
)
from src.repositories.ingestion_queue import ingestion_queue


async def register_external_document(db, values, *, reindex=False):
    identity = (values["source_system"], values["tenant_id"], values["external_document_id"])
    await lock_document_identity(db, "external", *identity)
    await reject_deleted_identity(db, "external", *identity)
    query = select(ExternalDocument).where(
        ExternalDocument.source_system == identity[0],
        ExternalDocument.tenant_id == identity[1],
        ExternalDocument.external_document_id == identity[2],
    )
    row = (await db.execute(query)).scalar_one_or_none()
    created = False
    if row is None:
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        result = await db.execute(insert(ExternalDocument).values(
            id=str(uuid.uuid4()), status="QUEUED", **values,
        ).on_conflict_do_nothing(index_elements=["source_system", "tenant_id", "external_document_id"]).returning(ExternalDocument.id))
        created = result.scalar_one_or_none() is not None
        row = (await db.execute(query)).scalar_one()
    if row.owner_subject_id != values.get("owner_subject_id") or row.collection_id != values.get("collection_id"):
        raise HTTPException(status_code=409, detail="Document identity is already assigned to another scope.")
    if not created and not reindex:
        immutable = ("storage_path", "mime_type", "file_size", "document_type")
        if any(getattr(row, key) != values.get(key) for key in immutable):
            raise HTTPException(status_code=409, detail="Document content changed; explicit reindex is required.")
    if not created and row.status == "READY" and any(
        getattr(row, key) != values.get(key) for key in ("storage_path", "mime_type", "file_size", "file_name", "document_type", "report_date")
    ):
        raise HTTPException(status_code=409, detail="Replacing indexed content requires a new document version.")
    # Lock the job before changing the existing document, matching publication order.
    job = await ingestion_queue.enqueue(db, "external", row.id, reset=reindex)
    await ingestion_queue.mirror_document_state(db, job)
    if reindex:
        for key, value in values.items():
            setattr(row, key, value)
        if not row.chunk_count:
            row.status = "QUEUED"
        row.processing_error = None
    await db.flush()
    return row, job
