"""Version provenance is committed with the vectors, active pointer and lease."""

from sqlalchemy import func, select

from src.inference.cohere_gateway import EMBEDDING_MODEL
from src.models.document_version import DocumentVersion


async def record_publication(db, job, source, processed):
    number = await db.scalar(select(func.coalesce(func.max(DocumentVersion.number), 0)).where(
        DocumentVersion.document_kind == source.kind, DocumentVersion.source_record_id == source.internal_id))
    row = DocumentVersion(document_kind=source.kind, source_record_id=source.internal_id, number=number + 1,
        job_id=job.id, generation=job.generation, client_id=source.client_id, tenant_id=source.tenant_id,
        collection_id=source.collection_id, subject_id=source.subject_id, storage_key=source.storage_key,
        mime_type=source.mime_type, file_size=source.file_size, source_sha256=processed.source_sha256,
        embedding_model=EMBEDDING_MODEL, chunk_count=len(processed.embedded.chunks))
    db.add(row)
    await db.flush()
    return row


async def list_published_versions(db, kind, source_record_id, limit=50, offset=0):
    """Caller must first authorize the live parent document; never return storage paths."""
    rows = (await db.execute(select(DocumentVersion).where(DocumentVersion.document_kind == kind,
        DocumentVersion.source_record_id == source_record_id).order_by(DocumentVersion.number.desc())
        .limit(limit).offset(offset))).scalars().all()
    return [{"version_id": row.id, "number": row.number, "source_sha256": row.source_sha256,
        "embedding_model": row.embedding_model, "chunk_count": row.chunk_count, "created_at": row.created_at} for row in rows]
