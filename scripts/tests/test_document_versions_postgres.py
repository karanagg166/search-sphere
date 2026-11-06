"""Published version identity and cutover fences using real pgvector, synthetic data."""

import hashlib
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import approve, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select
from src.db import get_db
from src.main import app
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.document_version import DocumentVersion
from src.models.ingestion_job import IngestionJob
from src.models.user import User
from src.repositories.ingestion_queue import IngestionQueue
from src.security.jwt import get_current_user
from src.security.query_access import query_inference
from src.services.conversation_context import select_conversation_context
from src.services.document_deletion import delete_registered_document
from src.services.document_service import DocumentService
from src.services.ingestion_pipeline import process_ingestion
from src.services.ingestion_publication import publish_ingestion
from src.services.ingestion_source import load_ingestion_source
from src.services.ingestion_worker import PostgresIngestionWorker
from src.storage.object_storage import get_object_storage
from src.vector_store.supabase_vector_store import SupabaseVectorStore
from test_conversation_context_postgres import conversation
from test_query_access_postgres import indexed_document, native_access


async def versions(fixture, doc_id):
    async with fixture.sessions() as db:
        return (await db.execute(select(DocumentVersion).where(DocumentVersion.source_record_id == doc_id)
            .order_by(DocumentVersion.number))).scalars().all()


@pytest.mark.asyncio
async def test_published_version_binds_original_bytes_vectors_and_active_document(fixture):
    doc, chunk = await indexed_document(fixture)
    rows = await versions(fixture, doc.id)
    assert len(rows) == 1
    version = rows[0]
    assert version.number == 1 and version.chunk_count == 1
    assert version.source_sha256 == hashlib.sha256(await fixture.storage.download(doc.storage_key)).hexdigest()
    assert version.embedding_model == "embed-english-light-v3.0"
    assert chunk.metadata_json["version_id"] == version.id
    async with fixture.sessions() as db:
        assert (await db.get(Document, doc.id)).current_version_id == version.id
    async with query_inference(native_access(fixture)) as access:
        result = await access.authorize_candidates([type_candidate(chunk)])
        assert result[0].version_id == version.id


@pytest.mark.asyncio
async def test_completed_native_turn_links_citation_and_memory_to_actual_version_and_message(fixture):
    from src.models.conversation_memory import ConversationMemory
    from src.models.message import Message
    from src.schemas.conversation import ConversationAnswerRequest
    from src.services.answer_service import AnswerService
    from src.services.conversation_service import ConversationService
    from src.services.search_service import SearchService
    from test_native_streams_postgres import setup

    value = await setup(fixture)
    access = native_access(fixture)
    access.conversation_id = value.cid
    async with fixture.sessions() as db, query_inference(access):
        service = ConversationService(db, AnswerService(SearchService(db, value.retriever, value.rewriter), value.generator))
        response = await service.answer_in_conversation(value.cid, ConversationAnswerRequest(query="Explain maternity policy"),
            value.user, buffer_stream=True)
    source = response.sources[0]
    assert source.version_id == value.row.metadata_json["version_id"] and source.chunk_id == value.row.point_id
    assert source.source_message_id == response.message.id
    async with fixture.sessions() as db:
        message = await db.get(Message, response.message.id)
        memory = await db.get(ConversationMemory, value.cid)
    assert message.sources[0]["version_id"] == source.version_id
    assert message.sources[0]["source_message_id"] == message.id
    compact = memory.state["topic_turns"][0]["sources"][0]
    assert compact["version_id"] == source.version_id and compact["source_message_id"] == message.id


def type_candidate(chunk):
    from test_query_access_postgres import candidate
    return candidate(chunk)


@pytest.mark.asyncio
async def test_failed_publication_rolls_back_version_and_preserves_searchable_old_index(fixture):
    doc, chunk = await indexed_document(fixture)
    original = (await versions(fixture, doc.id))[0]
    async with fixture.sessions() as db:
        await DocumentService(db, fixture.storage).reindex_user_document(doc.id, fixture.owner_id)
    async def invalid(source, storage, stage):
        processed = await process_ingestion(source, storage, stage)
        processed.embedded = replace(processed.embedded, chunks=[replace(processed.embedded.chunks[0], embedding=[1.0])])
        return processed
    assert await PostgresIngestionWorker(fixture.sessions, processor=invalid, storage_factory=lambda: fixture.storage).run_once()
    assert [version.id for version in await versions(fixture, doc.id)] == [original.id]
    async with fixture.sessions() as db:
        parent = await db.get(Document, doc.id)
        job = (await db.execute(select(IngestionJob).where(IngestionJob.document_id == doc.id))).scalar_one()
        assert parent.current_version_id == original.id and parent.status == "indexed" and job.status == "RETRY"
    async with query_inference(native_access(fixture)):
        rows = await SupabaseVectorStore(session_factory=fixture.sessions).search_sparse(query_vector=None, query_text="maternity", limit=20)
        assert [row.point_id for row in rows] == [chunk.point_id]


@pytest.mark.asyncio
async def test_successful_reindex_retains_provenance_and_prevents_stale_citation_reuse(fixture):
    doc, chunk = await indexed_document(fixture)
    access, messages = await conversation(fixture, chunk)
    prior_version = chunk.metadata_json["version_id"]
    async with fixture.sessions() as db, db.begin():
        from src.models.message import Message
        message = await db.get(Message, messages[1].id)
        message.sources = [{**message.sources[0], "version_id": prior_version}]
    async with fixture.sessions() as db:
        await DocumentService(db, fixture.storage).reindex_user_document(doc.id, fixture.owner_id)
    assert await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage).run_once()
    rows = await versions(fixture, doc.id)
    assert [row.number for row in rows] == [1, 2] and rows[0].source_sha256 == rows[1].source_sha256
    assert rows[0].id != rows[1].id
    async with query_inference(access):
        selection = await select_conversation_context(access, "Summarize your previous answer")
        assert selection.clarification and not selection.history
    # A stale version tag cannot be searched even if its root, model and content match.
    async with fixture.sessions() as db, db.begin():
        db.add(DocumentChunk(point_id=chunk.point_id + "-stale", document_id=doc.id, chunk_index=5,
            content=chunk.content, embedding=chunk.embedding, metadata_json={**chunk.metadata_json, "version_id": prior_version}))
    async with query_inference(native_access(fixture)):
        store = SupabaseVectorStore(session_factory=fixture.sessions)
        assert [row.point_id for row in await store.search_dense(query_vector=chunk.embedding, limit=20)] == [chunk.point_id]
        assert [row.point_id for row in await store.search_sparse(query_vector=None, query_text="maternity", limit=20)] == [chunk.point_id]


@pytest.mark.asyncio
async def test_lease_duplicate_cannot_create_second_published_version(fixture):
    await approve(fixture, subject=fixture.owner_id)
    doc = await upload_native(fixture)
    queue = IngestionQueue(fixture.sessions)
    lease = await queue.claim()
    async with fixture.sessions() as db:
        source = await load_ingestion_source(db, "standalone", doc.id)
    # Real worker processing is independently covered above. Reuse a completed
    # synthetic processing result to deliver the same lease twice at publication.
    from src.inference.policy import inference_scope
    async def stage(_):
        pass
    async with query_inference(native_access(fixture)):
        with inference_scope(source.classification, source.client_id, source.tenant_id):
            processed = await process_ingestion(source, fixture.storage, stage)
    assert await publish_ingestion(fixture.sessions, queue, lease, source, processed)
    assert not await publish_ingestion(fixture.sessions, queue, lease, source, processed)
    assert len(await versions(fixture, doc.id)) == 1


@pytest.mark.asyncio
async def test_version_http_listing_requires_parent_authorization_and_hides_storage_keys(fixture):
    doc, _chunk = await indexed_document(fixture)
    async def database():
        async with fixture.sessions() as db:
            yield db
    async with fixture.sessions() as db:
        user = await db.get(User, fixture.owner_id)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_object_storage] = lambda: fixture.storage
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/documents/{doc.id}/versions")
            assert response.status_code == 200
            payload = response.json()
            assert payload["current_version_id"] == payload["versions"][0]["version_id"]
            assert "storage_key" not in response.text and "documents/" not in response.text
            assert (await client.get(f"/documents/{doc.id}/versions?offset=1&limit=1")).json()["versions"] == []
            other_user = User(id="unowned-fixture-user", email="unowned@example.invalid", name="Synthetic")
            app.dependency_overrides[get_current_user] = lambda: other_user
            assert (await client.get(f"/documents/{doc.id}/versions")).status_code == 404
    finally:
        app.dependency_overrides.clear()
    async with fixture.sessions() as db, db.begin():
        await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.source_record_id == doc.id)) == 0
