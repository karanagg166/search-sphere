"""Provider timeouts release leases and reach a bounded terminal state."""

from datetime import UTC, datetime, timedelta

import pytest
from ingestion_test_support import approve, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import select, update
from src.inference.policy import ProviderTimeoutError
from src.models.document import Document
from src.models.ingestion_job import IngestionJob
from src.services.document_service import DocumentService
from src.services.ingestion_worker import PostgresIngestionWorker
from test_query_access_postgres import indexed_document


@pytest.mark.asyncio
@pytest.mark.parametrize("reindex", [False, True])
async def test_timeout_retries_release_lease_and_stop_after_three_attempts(fixture, reindex):
    if reindex:
        document, _ = await indexed_document(fixture)
        async with fixture.sessions() as db:
            await DocumentService(db, fixture.storage).reindex_user_document(document.id, fixture.owner_id)
    else:
        await approve(fixture, subject=fixture.owner_id)
        document = await upload_native(fixture)
    attempts = []
    async def timeout(source, storage, stage):
        attempts.append(source.internal_id)
        raise ProviderTimeoutError("Synthetic provider timeout; no source content sent")
    worker = PostgresIngestionWorker(fixture.sessions, processor=timeout, storage_factory=lambda: fixture.storage)
    for attempt in range(1, 4):
        assert await worker.run_once()
        async with fixture.sessions() as db, db.begin():
            job = (await db.execute(select(IngestionJob).where(IngestionJob.document_id == document.id))).scalar_one()
            assert job.attempts == attempt and job.error_code == "provider_timeout"
            assert job.status == ("RETRY" if attempt < 3 else "FAILED")
            assert job.lease_token is None and job.lease_expires_at is None
            assert (await db.get(Document, document.id)).status == ("indexed" if reindex else (
                "failed:provider_timeout" if attempt == 3 else "queued"))
            if attempt < 3:
                await db.execute(update(IngestionJob).where(IngestionJob.id == job.id).values(
                    available_at=datetime.now(UTC) - timedelta(seconds=1)))
    assert not await worker.run_once() and attempts == [document.id] * 3
