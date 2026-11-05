"""Real pgvector replacement, visibility, rollback and lease-fencing checks."""

import asyncio
import os
import uuid
from dataclasses import replace
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.models.document_chunk import DocumentChunk
from src.models.ingestion_job import IngestionJob
from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.repositories.ingestion_queue import IngestionQueue
from src.vector_store.supabase_vector_store import (
    SupabaseVectorStore,
    SupabaseVectorStoreError,
)


def document(marker):
    return EmbeddedDocument([EmbeddedChunk(
        chunk_index=0, content=marker, token_count=4, start_page=1, end_page=1,
        page_numbers=[1], block_types=["text"], embedding=[1.0] + [0.0] * 383,
    )])


@pytest_asyncio.fixture
async def index():
    url = os.environ.get("INGESTION_TEST_DATABASE_URL")
    assert url, "INGESTION_TEST_DATABASE_URL is required for the pgvector release gate"
    parsed = urlsplit(url)
    assert parsed.hostname in {"localhost", "127.0.0.1", "host.docker.internal", "postgres"}
    assert parsed.path in {"/ss_test", "/search_sphere_test"}
    engine = create_async_engine(url, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    store = SupabaseVectorStore(session_factory=sessions)
    doc_id = str(uuid.uuid4())
    yield store, sessions, doc_id
    async with sessions() as db, db.begin():
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        await db.execute(delete(IngestionJob).where(IngestionJob.document_id == doc_id))
    await engine.dispose()


async def contents(sessions, doc_id):
    async with sessions() as db:
        return list((await db.execute(
            select(DocumentChunk.content).where(DocumentChunk.document_id == doc_id)
        )).scalars())


@pytest.mark.asyncio
async def test_database_insert_failure_preserves_previous_index(index):
    store, sessions, doc_id = index
    await store.index_document(doc_id, document("PUBLIC OLD VERIFIED INDEX"))
    bad = document("MUST-NOT-LEAK-FAILED-INPUT")
    bad = EmbeddedDocument([replace(bad.chunks[0], embedding=[1.0, 2.0])])
    with pytest.raises(SupabaseVectorStoreError) as error:
        await store.index_document(doc_id, bad)
    assert "MUST-NOT-LEAK" not in str(error.value)
    assert await contents(sessions, doc_id) == ["PUBLIC OLD VERIFIED INDEX"]


@pytest.mark.asyncio
async def test_replacement_is_invisible_until_commit_and_caller_rollback_is_respected(index):
    store, sessions, doc_id = index
    await store.index_document(doc_id, document("PUBLIC OLD INDEX"))
    async with sessions() as writer, writer.begin() as transaction:
        await store.index_document(doc_id, document("PUBLIC NEW INDEX"), session=writer)
        assert await contents(sessions, doc_id) == ["PUBLIC OLD INDEX"]
        await transaction.rollback()
    assert await contents(sessions, doc_id) == ["PUBLIC OLD INDEX"]
    async with sessions() as writer, writer.begin():
        await store.index_document(doc_id, document("PUBLIC NEW INDEX"), session=writer)
    assert await contents(sessions, doc_id) == ["PUBLIC NEW INDEX"]


@pytest.mark.asyncio
async def test_duplicate_concurrent_delivery_serializes_without_lost_index(index):
    store, sessions, doc_id = index
    assert await asyncio.gather(*(
        store.index_document(doc_id, document("PUBLIC DUPLICATE FIXTURE")) for _ in range(8)
    )) == [1] * 8
    assert await contents(sessions, doc_id) == ["PUBLIC DUPLICATE FIXTURE"]


@pytest.mark.asyncio
async def test_same_document_id_is_isolated_across_client_tenant_and_native_scopes(index):
    store, sessions, doc_id = index
    first = {"client_id": "fixture-client", "tenant_id": "fixture-school-a"}
    second = {"client_id": "fixture-client", "tenant_id": "fixture-school-b"}
    await store.index_document(doc_id, document("PUBLIC NATIVE"))
    await store.index_document(doc_id, document("PUBLIC SCHOOL A"), extra_payload=first)
    await store.index_document(doc_id, document("PUBLIC SCHOOL B"), extra_payload=second)
    await store.index_document(doc_id, document("PUBLIC SCHOOL A REPLACEMENT"), extra_payload=first)
    assert set(await contents(sessions, doc_id)) == {
        "PUBLIC NATIVE", "PUBLIC SCHOOL A REPLACEMENT", "PUBLIC SCHOOL B",
    }
    await store.index_document(doc_id, EmbeddedDocument([]), extra_payload=first)
    assert set(await contents(sessions, doc_id)) == {"PUBLIC NATIVE", "PUBLIC SCHOOL B"}


@pytest.mark.asyncio
async def test_incomplete_external_scope_is_rejected_without_mutation(index):
    store, sessions, doc_id = index
    await store.index_document(doc_id, document("PUBLIC ORIGINAL"))
    for incomplete in ({"client_id": "fixture-client"}, {"source_system": "fixture-client"}):
        with pytest.raises(SupabaseVectorStoreError, match="scope"):
            await store.index_document(doc_id, document("PUBLIC INVALID"), extra_payload=incomplete)
    assert await contents(sessions, doc_id) == ["PUBLIC ORIGINAL"]


@pytest.mark.asyncio
async def test_job_terminal_status_and_vectors_share_one_fenced_commit(index):
    store, sessions, doc_id = index
    queue = IngestionQueue(sessions)
    async with sessions() as db, db.begin():
        await queue.enqueue(db, "standalone", doc_id)
    lease = await queue.claim()
    assert lease and lease.document_id == doc_id
    async with sessions() as db, db.begin() as transaction:
        job = await queue.lock_lease(db, lease)
        assert job
        await store.index_document(doc_id, document("PUBLIC UNCOMMITTED"), session=db)
        job.status, job.stage = "SUCCEEDED", "complete"
        await db.flush()
        assert await contents(sessions, doc_id) == []
        await transaction.rollback()
    async with sessions() as db:
        job = await db.get(IngestionJob, lease.job_id)
        assert job.status == "RUNNING"
    async with sessions() as db, db.begin():
        job = await queue.lock_lease(db, lease)
        assert job
        await store.index_document(doc_id, document("PUBLIC COMMITTED"), session=db)
        job.status, job.stage = "SUCCEEDED", "complete"
        job.lease_token = job.lease_expires_at = None
    assert await contents(sessions, doc_id) == ["PUBLIC COMMITTED"]
    async with sessions() as db, db.begin():
        assert await queue.lock_lease(db, lease) is None
