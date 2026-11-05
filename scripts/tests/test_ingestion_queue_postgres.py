"""Concurrency and process-loss regressions against migrated PostgreSQL/pgvector."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.models.ingestion_job import IngestionJob
from src.repositories.ingestion_queue import IngestionQueue


@pytest_asyncio.fixture
async def queue():
    url = os.environ.get("INGESTION_TEST_DATABASE_URL")
    assert url, "INGESTION_TEST_DATABASE_URL is required for the ingestion release gate"
    parsed = urlsplit(url)
    assert parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "host.docker.internal",
        "postgres",
    }
    assert parsed.path in {"/ss_test", "/search_sphere_test"}
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = IngestionQueue(sessions)
    # The dedicated test database contains synthetic test-owned work only.
    async with sessions() as db, db.begin():
        await db.execute(delete(IngestionJob))
    yield repository
    async with sessions() as db, db.begin():
        await db.execute(delete(IngestionJob))
    await engine.dispose()


async def register(queue, kind="external"):
    document_id = str(uuid.uuid4())
    async with queue.sessions() as db, db.begin():
        job = await queue.enqueue(db, kind, document_id)
    return job, document_id


@pytest.mark.asyncio
async def test_registration_is_atomic_and_duplicate_delivery_is_idempotent(queue):
    job, document_id = await register(queue)

    async def duplicate():
        async with queue.sessions() as db, db.begin():
            return (await queue.enqueue(db, "external", document_id)).id

    assert set(await asyncio.gather(*(duplicate() for _ in range(8)))) == {job.id}
    async with queue.sessions() as db:
        transaction = await db.begin()
        await queue.enqueue(db, "standalone", str(uuid.uuid4()))
        await transaction.rollback()
    async with queue.sessions() as db:
        assert len((await db.execute(select(IngestionJob))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_concurrent_workers_cannot_claim_the_same_document(queue):
    for kind in ("standalone", "external"):
        await register(queue, kind)
    claims = await asyncio.gather(*(queue.claim() for _ in range(8)))
    leases = [lease for lease in claims if lease]
    assert len(leases) == 2
    assert len({lease.job_id for lease in leases}) == 2
    assert {lease.document_kind for lease in leases} == {"standalone", "external"}


@pytest.mark.asyncio
async def test_expired_worker_recovers_after_restart_and_is_fenced(queue):
    await register(queue)
    old = await queue.claim()
    assert old
    async with queue.sessions() as db, db.begin():
        await db.execute(
            update(IngestionJob)
            .where(IngestionJob.id == old.job_id)
            .values(
                lease_expires_at=datetime.now(UTC) - timedelta(seconds=1),
            )
        )
    # New repository instance represents an independent restarted process.
    restarted = IngestionQueue(queue.sessions)
    recovered = await restarted.claim()
    assert recovered and recovered.job_id == old.job_id and recovered.attempt == 2
    assert recovered.token != old.token
    assert not await queue.heartbeat(old)
    assert not await queue.fail(old, "processing_failed", retryable=True)
    async with queue.sessions() as db, db.begin():
        assert await queue.lock_lease(db, old) is None
        assert await queue.lock_lease(db, recovered) is not None


@pytest.mark.asyncio
async def test_live_lease_survives_deployment_handoff(queue):
    await register(queue)
    lease = await queue.claim()
    assert lease and await queue.heartbeat(lease)
    assert await IngestionQueue(queue.sessions).claim() is None


@pytest.mark.asyncio
async def test_retries_are_delayed_bounded_and_terminal_reason_is_preserved(queue):
    job, _ = await register(queue)
    for attempt in range(1, 4):
        lease = await queue.claim()
        assert lease and lease.attempt == attempt
        assert await queue.fail(lease, "provider_quota", retryable=True)
        assert await queue.claim() is None
        async with queue.sessions() as db, db.begin():
            current = await db.get(IngestionJob, job.id)
            assert current.error_code == "provider_quota"
            assert current.status == ("RETRY" if attempt < 3 else "FAILED")
            current.available_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await queue.claim() is None


@pytest.mark.asyncio
async def test_explicit_reindex_invalidates_old_worker_and_duplicate_does_not(queue):
    _, document_id = await register(queue)
    old = await queue.claim()
    assert old
    async with queue.sessions() as db, db.begin():
        same = await queue.enqueue(db, "external", document_id)
        assert same.lease_token == old.token
    async with queue.sessions() as db, db.begin():
        await queue.enqueue(db, "external", document_id, reset=True)
    assert not await queue.heartbeat(old)
    new = await queue.claim()
    assert new and new.attempt == 1 and new.token != old.token


@pytest.mark.asyncio
async def test_completed_job_rejects_stale_worker_mutations(queue):
    job, _ = await register(queue)
    lease = await queue.claim()
    assert lease
    async with queue.sessions() as db, db.begin():
        current = await queue.lock_lease(db, lease)
        assert current
        current.status = "SUCCEEDED"
    assert not await queue.stage(lease, "indexing")
    assert not await queue.fail(lease, "processing_failed", retryable=True)
    async with queue.sessions() as db:
        assert (await db.get(IngestionJob, job.id)).status == "SUCCEEDED"
