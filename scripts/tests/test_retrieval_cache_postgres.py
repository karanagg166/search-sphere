"""Real SQL cache isolation and invalidation; provider and retrieval data synthetic."""

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import delete, func, select, update
from src.config import settings
from src.inference.policy import ProviderNotApprovedError
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.retrieval_cache import RetrievalCache
from src.models.user import User
from src.processing.models.search import RerankedSearchResult
from src.retrieval.permission_cache import PermissionRetrievalCache, invalidate_scope
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.security.query_access import QueryAccess, query_inference
from src.services.conversation_context import select_conversation_context
from src.services.delegated_conversations import register_conversation
from src.services.document_deletion import delete_registered_document
from test_conversation_context_postgres import conversation
from test_delegated_conversations_postgres import identity
from test_query_access_postgres import candidate, indexed_document, native_access


def result(row):
    return RerankedSearchResult(point_id=row.point_id, document_id=row.document_id, chunk_index=row.chunk_index,
        content=row.content, token_count=row.token_count, start_page=row.start_page, end_page=row.end_page,
        page_numbers=row.page_numbers, block_types=row.block_types, rerank_score=0.9, rrf_score=0.1, rank=1)


@pytest.mark.asyncio
async def test_repeat_search_skips_embedding_and_rerank_without_caching_passages(fixture):
    doc, row = await indexed_document(fixture)
    hybrid = type("Hybrid", (), {"search": AsyncMock(return_value=[candidate(row)])})()
    reranker = type("Reranker", (), {"arerank": AsyncMock(return_value=[result(row)])})()
    retriever = RerankedHybridRetriever(hybrid_retriever=hybrid, reranker=reranker)
    for _ in range(2):
        async with query_inference(native_access(fixture, doc.id)):
            answer = await retriever.search("PUBLIC SYNTHETIC MATERNITY POLICY")
            assert answer[0].content == row.content
    assert hybrid.search.await_count == reranker.arerank.await_count == 1
    async with fixture.sessions() as db:
        cached = (await db.execute(select(RetrievalCache).where(RetrievalCache.tenant_id == fixture.owner_id))).scalar_one()
    stored = json.dumps(cached.entries)
    assert row.content not in stored and "MATERNITY" not in stored
    assert cached.entries[0]["sha256"] == hashlib.sha256(row.content.encode()).hexdigest()


@pytest.mark.asyncio
async def test_conversation_actor_context_classification_and_query_options_isolate_keys(fixture):
    _doc, _row = await indexed_document(fixture)
    async def key(access, parameters=None):
        async with query_inference(access):
            return (await PermissionRetrievalCache.prepare(access, parameters or {"query": "public"})).key
    original = native_access(fixture)
    original_key = await key(original)
    assert original_key != await key(native_access(fixture), {"query": "public", "top_k": 1})
    other_operation = native_access(fixture)
    other_operation.operation = "answers:generate"
    assert original_key != await key(other_operation)
    first, _ = await conversation(fixture)
    second, _ = await conversation(fixture)
    assert await key(first) != await key(second)
    before_history = await key(first)
    async with query_inference(first):
        await select_conversation_context(first, "What about interns?")
        with_history = await PermissionRetrievalCache.prepare(first, {"query": "public"})
        assert with_history.key != before_history
    # A delegated caller without an authenticated actor never participates in caching.
    access = native_access(fixture)
    access.context = replace(access.context, is_service_client=True, actor_id=None)
    assert await PermissionRetrievalCache.prepare(access, {"query": "public"}) is None


@pytest.mark.asyncio
async def test_delegated_cache_is_actor_scoped_and_client_revocation_is_enforced(fixture):
    context, _, digest = await identity(fixture)
    await approve(fixture, context.client_id, context.tenant_id, context.subject_id, context.collection_id)
    async with fixture.sessions() as db, db.begin():
        doc = ExternalDocument(source_system=context.client_id, tenant_id=context.tenant_id,
            collection_id=context.collection_id, owner_subject_id=context.subject_id,
            external_document_id="public-cache-fixture", external_patient_id=fixture.owner_id,
            storage_path="synthetic/public.pdf", file_name="public.pdf", mime_type="application/pdf", file_size=10,
            document_type="PUBLIC", status="READY")
        db.add(doc)
        await db.flush()
        fixture.external_ids.append(doc.id)
        row = DocumentChunk(point_id=fixture.owner_id, document_id=doc.external_document_id, chunk_index=0,
            content="PUBLIC SYNTHETIC SCOPED EVIDENCE", embedding=[1.0] + [0.0] * 383, client_id=context.client_id,
            tenant_id=context.tenant_id, collection_id=context.collection_id, owner_subject_id=context.subject_id,
            metadata_json={"embedding_model": "embed-english-light-v3.0", "source_record_id": doc.id})
        db.add(row)
    accesses = []
    for actor in ["doctor-a", "doctor-b"]:
        actor_context = replace(context, actor_id=actor)
        async with fixture.sessions() as db, db.begin():
            cid = (await register_conversation(db, actor_context, "same-reference")).id
        accesses.append(QueryAccess(fixture.sessions, actor_context, "answers:generate", key_hash=digest, conversation_id=cid))
    async with query_inference(accesses[0]):
        cached = await PermissionRetrievalCache.prepare(accesses[0], {"query": "public"})
        await cached.put([result(row)])
    async with query_inference(accesses[1]):
        other = await PermissionRetrievalCache.prepare(accesses[1], {"query": "public"})
        assert other.key != cached.key and await other.get() is None
    from src.models.service_client import ServiceClient
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(ServiceClient).where(ServiceClient.client_id == fixture.owner_id).values(allowed_scopes=[]))
    async with query_inference(accesses[0]):
        with pytest.raises(ProviderNotApprovedError):
            await cached.get()


@pytest.mark.asyncio
async def test_changed_source_bytes_and_new_chunks_cannot_reuse_old_results(fixture):
    _doc, row = await indexed_document(fixture)
    async with query_inference(native_access(fixture)) as access:
        cached = await PermissionRetrievalCache.prepare(access, {"query": "public"})
        await cached.put([result(row)])
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(DocumentChunk).where(DocumentChunk.id == row.id).values(content="PUBLIC UPDATED SOURCE"))
        assert await cached.get() is None
        # Even unchanged row identity cannot bypass the source-content fingerprint.
    async with query_inference(native_access(fixture)) as access:
        before = await PermissionRetrievalCache.prepare(access, {"query": "nothing"})
        await before.put([])
        async with fixture.sessions() as db, db.begin():
            db.add(DocumentChunk(point_id=row.point_id + "-new", document_id=row.document_id, chunk_index=1,
                content="PUBLIC NEW EVIDENCE", embedding=row.embedding, metadata_json=row.metadata_json))
        after = await PermissionRetrievalCache.prepare(access, {"query": "nothing"})
        assert after.key != before.key and await after.get() is None


@pytest.mark.asyncio
async def test_cache_does_not_bypass_user_or_provider_policy_revocation(fixture):
    _doc, row = await indexed_document(fixture)
    async with query_inference(native_access(fixture)) as access:
        cached = await PermissionRetrievalCache.prepare(access, {"query": "public"})
        await cached.put([result(row)])
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=False))
        with pytest.raises(ProviderNotApprovedError):
            await cached.get()
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=True))
        await db.execute(update(User).where(User.id == fixture.owner_id).values(is_active=False))
    async with query_inference(native_access(fixture)):
        with pytest.raises(ProviderNotApprovedError):
            await PermissionRetrievalCache.prepare(native_access(fixture), {"query": "public"})


@pytest.mark.asyncio
async def test_free_trial_eligibility_changes_disable_cache_hits(fixture, monkeypatch):
    _doc, row = await indexed_document(fixture)
    async with query_inference(native_access(fixture)) as access:
        cached = await PermissionRetrievalCache.prepare(access, {"query": "public"})
        await cached.put([result(row)])
        monkeypatch.setattr(settings, "COHERE_TRIAL_KEY_SHA256", "unapproved")
        assert await PermissionRetrievalCache.prepare(access, {"query": "public"}) is None
        with pytest.raises(ProviderNotApprovedError):
            await cached.get()


@pytest.mark.asyncio
async def test_tampering_expiry_and_deletion_remove_cache_entries(fixture):
    doc, row = await indexed_document(fixture)
    async with query_inference(native_access(fixture)) as access:
        cached = await PermissionRetrievalCache.prepare(access, {"query": "public"})
        await cached.put([result(row)])
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(RetrievalCache).where(RetrievalCache.key == cached.key).values(entries=[]))
        assert await cached.get() is None
        await cached.put([result(row)])
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(RetrievalCache).where(RetrievalCache.key == cached.key).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        assert await cached.get() is None
        await cached.put([result(row)])
    async with fixture.sessions() as db, db.begin():
        await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
    async with fixture.sessions() as db:
        assert (await db.execute(select(func.count()).select_from(RetrievalCache).where(
            RetrievalCache.tenant_id == fixture.owner_id))).scalar_one() == 0


@pytest.mark.asyncio
async def test_invalidation_shares_rollback_and_global_capacity_is_bounded(fixture, monkeypatch):
    import src.retrieval.permission_cache as cache_module

    _doc, row = await indexed_document(fixture)
    monkeypatch.setattr(cache_module, "MAX_ENTRIES", 4)
    async with fixture.sessions() as db, db.begin():
        await db.execute(delete(RetrievalCache))  # Dedicated synthetic DB.
    async with query_inference(native_access(fixture)) as access:
        caches = [await PermissionRetrievalCache.prepare(access, {"query": f"public {index}"}) for index in range(12)]
        await asyncio.gather(*(cached.put([result(row)]) for cached in caches))
        async with fixture.sessions() as db:
            assert (await db.execute(select(func.count()).select_from(RetrievalCache))).scalar_one() == 4
        cached = caches[-1]
        await cached.put([result(row)])
        async with fixture.sessions() as db, db.begin() as transaction:
            await invalidate_scope(db, "search_sphere", fixture.owner_id)
            await transaction.rollback()
        assert await cached.get() is not None
        async with fixture.sessions() as db, db.begin():
            await invalidate_scope(db, "search_sphere", fixture.owner_id)
        assert await cached.get() is None


@pytest.mark.asyncio
async def test_large_scope_uses_uncached_retrieval(fixture, monkeypatch):
    await indexed_document(fixture)
    monkeypatch.setattr("src.retrieval.permission_cache.MAX_SCOPE_CHUNKS", 0)
    async with query_inference(native_access(fixture)) as access:
        assert await PermissionRetrievalCache.prepare(access, {"query": "public"}) is None
