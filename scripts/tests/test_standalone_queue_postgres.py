"""Retired client jobs cannot consume standalone processing or change state."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from ingestion_test_support import approve, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import select
from src.models.document import Document
from src.models.ingestion_job import IngestionJob
from src.services.ingestion_worker import PostgresIngestionWorker


@pytest.mark.asyncio
async def test_native_worker_leaves_queued_and_expired_client_jobs_untouched(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    old = datetime.now(UTC) - timedelta(hours=1)
    async with fixture.sessions() as db, db.begin():
        queued = IngestionJob(document_kind="external", document_id=str(uuid.uuid4()),
                              status="QUEUED", available_at=old, attempts=0)
        expired = IngestionJob(document_kind="external", document_id=str(uuid.uuid4()),
                               status="RUNNING", lease_token=str(uuid.uuid4()),
                               lease_expires_at=old, available_at=old, attempts=3)
        db.add_all([queued, expired])
        await db.flush()
        ids, token = [queued.id, expired.id], expired.lease_token
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert worker.queue.document_kinds == ("standalone",)
    assert await worker.run_once()
    assert not await worker.run_once()
    async with fixture.sessions() as db:
        assert (await db.get(Document, document.id)).status == "indexed"
        rows = {row.id: row for row in (await db.scalars(select(IngestionJob).where(IngestionJob.id.in_(ids)))).all()}
        assert rows[ids[0]].status == "QUEUED" and rows[ids[0]].attempts == 0
        assert rows[ids[1]].status == "RUNNING" and rows[ids[1]].attempts == 3
        assert rows[ids[1]].lease_token == token
    assert fixture.calls
