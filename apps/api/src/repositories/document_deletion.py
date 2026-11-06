"""Identity locks, deletion scopes, and receipts shared by all document APIs."""

import json

from fastapi import HTTPException
from sqlalchemy import func, select, text

from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion


async def lock_document_identity(db, kind, client_id, tenant_id, document_id):
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"), {
            "identity": "document-lifecycle:" + json.dumps([kind, client_id, tenant_id, document_id]),
        })


def deletion_identity(kind, client_id, tenant_id, document_id):
    return (
        DocumentDeletion.kind == kind, DocumentDeletion.client_id == client_id,
        DocumentDeletion.tenant_id == tenant_id, DocumentDeletion.document_id == document_id,
    )


async def find_deletion(db, kind, client_id, tenant_id, document_id, *, context=None):
    row = (await db.execute(select(DocumentDeletion).where(*deletion_identity(kind, client_id, tenant_id, document_id)))).scalar_one_or_none()
    if row is not None and context:
        context.resolve_scope(row.collection_id, row.subject_id)
    return row


async def reject_deleted_identity(db, kind, client_id, tenant_id, document_id):
    if await find_deletion(db, kind, client_id, tenant_id, document_id):
        raise HTTPException(status_code=409, detail="Document was deleted. Use a new document identity.")


def chunk_scope(row):
    predicate = [DocumentChunk.document_id == row.document_id]
    if row.kind == "standalone":
        predicate += [DocumentChunk.client_id.is_(None), DocumentChunk.source_system.is_(None)]
    else:
        predicate += [DocumentChunk.client_id == row.client_id, DocumentChunk.tenant_id == row.tenant_id]
    return predicate


async def cleanup_receipt(db, row):
    if row is None:
        raise HTTPException(status_code=404, detail="Document cleanup receipt not found.")
    count = (await db.execute(select(func.count()).select_from(DocumentChunk).where(*chunk_scope(row)))).scalar_one()
    pending_assets = (await db.execute(select(func.count()).select_from(DocumentDeletion).where(
        DocumentDeletion.kind == "asset", DocumentDeletion.source_record_id == row.source_record_id,
        DocumentDeletion.client_id == row.client_id, DocumentDeletion.tenant_id == row.tenant_id,
        DocumentDeletion.status != "COMPLETE",
    ))).scalar_one()
    return {
        "document_id": row.document_id,
        "status": "complete" if row.status == "COMPLETE" and row.object_deleted and count == 0 and pending_assets == 0 else "pending",
        "vectors_remaining": count, "object_deleted": row.object_deleted,
        "derived_objects_remaining": pending_assets,
        "attempts": row.attempts, "error_code": row.error_code,
    }
