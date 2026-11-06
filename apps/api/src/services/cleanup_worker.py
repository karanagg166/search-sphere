"""Leased, bounded-attempt cleanup; unfinished deletion survives restarts."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, or_, select, update

from src.db import AsyncSessionLocal
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.ingestion_job import IngestionJob
from src.repositories.document_deletion import chunk_scope
from src.storage.object_storage import get_object_storage


class DocumentCleanupWorker:
    def __init__(self, sessions=AsyncSessionLocal, storage_factory=get_object_storage):
        self.sessions, self.storage_factory = sessions, storage_factory

    async def run_once(self, deletion_id=None):
        async with self.sessions() as db, db.begin():
            live_job = exists(select(IngestionJob.id).where(
                IngestionJob.document_id == DocumentDeletion.source_record_id,
                IngestionJob.lease_token == DocumentDeletion.lease_token,
                IngestionJob.status == "RUNNING", IngestionJob.lease_expires_at > func.now(),
            ))
            await db.execute(update(DocumentDeletion).where(
                DocumentDeletion.kind == "asset", DocumentDeletion.status == "STAGED",
                DocumentDeletion.available_at <= func.now(), ~live_job,
            ).values(status="QUEUED", lease_token=None))
            query = select(DocumentDeletion).where(or_(
                (DocumentDeletion.status == "QUEUED") & (DocumentDeletion.available_at <= func.now()),
                (DocumentDeletion.status == "PROCESSING") & (DocumentDeletion.lease_expires_at <= func.now()),
            ))
            if deletion_id:
                query = query.where(DocumentDeletion.id == deletion_id)
            row = (await db.execute(query.order_by(DocumentDeletion.available_at).limit(1).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row is None:
                return False
            row.status, row.lease_token = "PROCESSING", str(uuid.uuid4())
            row.lease_expires_at = datetime.now(UTC) + timedelta(seconds=90)
            row.attempts += 1
            cleanup_id, token, storage_key = row.id, row.lease_token, row.storage_key
        error_code = None
        try:
            async with asyncio.timeout(45):
                storage = self.storage_factory()
                # Do not trust DELETE success alone. HEAD must confirm absence.
                if not await storage.delete(storage_key) or await storage.exists(storage_key):
                    raise RuntimeError("object_cleanup_unverified")
        except Exception:
            error_code = "object_cleanup_unverified"
        async with self.sessions() as db, db.begin():
            row = (await db.execute(select(DocumentDeletion).where(
                DocumentDeletion.id == cleanup_id, DocumentDeletion.lease_token == token,
                DocumentDeletion.lease_expires_at > func.now(),
            ).with_for_update())).scalar_one_or_none()
            if row is None:
                return True  # Another process owns recovery of this expired attempt.
            if row.kind != "asset":
                await db.execute(delete(DocumentChunk).where(*chunk_scope(row)))
            row.lease_token = row.lease_expires_at = None
            row.object_deleted, row.error_code = error_code is None, error_code
            row.status = "COMPLETE" if error_code is None else "QUEUED"
            row.available_at = datetime.now(UTC) + timedelta(seconds=min(3600, 10 * 2 ** min(row.attempts - 1, 9)))
        return True
