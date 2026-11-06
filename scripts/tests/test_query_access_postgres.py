"""Real SQL scopes and pre-provider candidate authorization; all data/providers synthetic."""

import hashlib
import uuid
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ingestion_test_support import approve, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import select, update
from src.inference.cohere_gateway import CohereGateway
from src.inference.policy import ProviderNotApprovedError
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.service_client import ServiceClient
from src.models.user import User
from src.processing.models.search import HybridSearchResult
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.security.query_access import QueryAccess, query_inference
from src.security.service_context import ServiceContext
from src.services.document_deletion import delete_registered_document
from src.services.ingestion_worker import PostgresIngestionWorker
from src.vector_store.supabase_vector_store import SupabaseVectorStore


async def indexed_document(fixture):
    await approve(fixture, subject=fixture.owner_id)
    doc = await upload_native(fixture)
    await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage).run_once()
    async with fixture.sessions() as db:
        row = (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))).scalar_one()
    return doc, row


def native_access(fixture, document_id=None):
    return QueryAccess(fixture.sessions, ServiceContext(
        "search_sphere", fixture.owner_id, subject_id=fixture.owner_id, user_id=fixture.owner_id, is_service_client=False,
    ), "search:execute", document_id=document_id)


def candidate(row, content=None):
    return HybridSearchResult(row.point_id, 0.9, row.document_id, row.chunk_index, content or row.content,
                              row.token_count, row.start_page, row.end_page, row.page_numbers, row.block_types,
                              client_id=row.client_id, tenant_id=row.tenant_id)


@pytest.mark.asyncio
async def test_sql_filters_exclude_old_models_and_colliding_external_chunks(fixture):
    doc, row = await indexed_document(fixture)
    async with fixture.sessions() as db, db.begin():
        for suffix, client, model in [("old", None, "all-MiniLM-L6-v2"), ("external", "another-client", "embed-english-light-v3.0")]:
            db.add(DocumentChunk(
                point_id=row.point_id + suffix, document_id=doc.id, chunk_index=5, content="PUBLIC SYNTHETIC MATERNITY POLICY",
                embedding=row.embedding, client_id=client, tenant_id="another-tenant" if client else None,
                metadata_json={"embedding_model": model, "source_record_id": doc.id},
            ))
    store = SupabaseVectorStore(session_factory=fixture.sessions)
    async with query_inference(native_access(fixture, doc.id)):
        dense = await store.search_dense(query_vector=row.embedding, limit=20)
        lexical = await store.search_sparse(query_vector=None, query_text="maternity", limit=20)
    assert [item.point_id for item in dense] == [row.point_id]
    assert [item.point_id for item in lexical] == [row.point_id]


@pytest.mark.asyncio
async def test_candidate_payload_is_refetched_before_rerank_and_unknown_ids_are_dropped(fixture):
    doc, row = await indexed_document(fixture)
    poisoned = candidate(row, "Untrusted retriever supplied another patient's text")
    missing = replace(poisoned, point_id="unowned-point")
    reranker = type("SyntheticReranker", (), {"arerank": AsyncMock(return_value=[])})()
    hybrid = type("SyntheticHybrid", (), {"search": AsyncMock(return_value=[poisoned, missing])})()
    retriever = RerankedHybridRetriever(hybrid_retriever=hybrid, reranker=reranker)
    async with query_inference(native_access(fixture, doc.id)):
        await retriever.search("PUBLIC SYNTHETIC", top_k=5, candidate_k=20)
    approved = reranker.arerank.await_args.kwargs["candidates"]
    assert len(approved) == 1 and approved[0].content == row.content
    assert "another patient's" not in approved[0].content


@pytest.mark.asyncio
async def test_source_deletion_and_data_policy_revocation_deny_next_provider_call(fixture):
    doc, row = await indexed_document(fixture)
    access = native_access(fixture, doc.id)
    async with query_inference(access):
        assert await access.authorize_candidates([candidate(row)])
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=False))
        with pytest.raises(ProviderNotApprovedError):
            await CohereGateway().embed(["PUBLIC SYNTHETIC"])
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=True))
    async with query_inference(native_access(fixture, doc.id)) as access:
        await access.authorize_candidates([candidate(row)])
        async with fixture.sessions() as db:
            await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
            await db.commit()
        with pytest.raises(ProviderNotApprovedError):
            await CohereGateway().embed(["PUBLIC SYNTHETIC"])


@pytest.mark.asyncio
async def test_revocation_during_provider_response_prevents_returning_the_result(fixture, monkeypatch):
    await approve(fixture, subject=fixture.owner_id)
    async def provider(self, endpoint, payload):
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(User).where(User.id == fixture.owner_id).values(is_active=False))
        return {"embeddings": {"float": [[1.0] + [0.0] * 383]}}
    monkeypatch.setattr(CohereGateway, "_request", provider)
    async with query_inference(native_access(fixture)):
        with pytest.raises(ProviderNotApprovedError):
            await CohereGateway().embed(["PUBLIC SYNTHETIC"])


@pytest.mark.asyncio
async def test_real_default_reranker_requires_query_authorization(fixture):
    _doc, row = await indexed_document(fixture)
    hybrid = type("SyntheticHybrid", (), {"search": AsyncMock(return_value=[candidate(row)])})()
    with pytest.raises(ProviderNotApprovedError, match="authorization is required"):
        await RerankedHybridRetriever(hybrid_retriever=hybrid).search("PUBLIC", top_k=5)


@pytest.mark.asyncio
async def test_delegated_collection_scope_and_client_revocation_are_rechecked(fixture):
    key_hash = hashlib.sha256(b"synthetic-delegated-key").hexdigest()
    await approve(fixture, fixture.owner_id, "synthetic-school", fixture.owner_id, "class-a")
    async with fixture.sessions() as db, db.begin():
        client = ServiceClient(client_id=fixture.owner_id, name="Synthetic school client", api_key_hash=key_hash,
                               api_key_prefix="synthetic", allowed_scopes=["search:execute"], authorized_tenants=["synthetic-school"])
        db.add(client)
        for collection in ["class-a", "class-b"]:
            doc = ExternalDocument(source_system=fixture.owner_id, tenant_id="synthetic-school", collection_id=collection,
                owner_subject_id=fixture.owner_id, external_document_id="public-" + collection, external_patient_id=fixture.owner_id,
                storage_path="public/" + collection, file_name="public.pdf", mime_type="application/pdf", file_size=10,
                document_type="PUBLIC", status="READY")
            db.add(doc)
            await db.flush()
            fixture.external_ids.append(doc.id)
            db.add(DocumentChunk(point_id=str(uuid.uuid4()), document_id=doc.external_document_id, chunk_index=0,
                content="PUBLIC SYNTHETIC", embedding=[1.0] + [0.0] * 383, client_id=fixture.owner_id,
                tenant_id="synthetic-school", collection_id=collection, owner_subject_id=fixture.owner_id,
                metadata_json={"embedding_model": "embed-english-light-v3.0", "source_record_id": doc.id}))
    access = QueryAccess(fixture.sessions, ServiceContext(fixture.owner_id, "synthetic-school", subject_id=fixture.owner_id,
                        collection_id="class-a", scopes={"search:execute"}), "search:execute", key_hash=key_hash)
    async with fixture.sessions() as db:
        rows = (await db.execute(select(DocumentChunk).where(DocumentChunk.client_id == fixture.owner_id))).scalars().all()
    async with query_inference(access):
        permitted = await access.authorize_candidates([candidate(row) for row in rows])
        assert len(permitted) == 1 and permitted[0].collection_id == "class-a"
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(ServiceClient).where(ServiceClient.client_id == fixture.owner_id).values(allowed_scopes=[]))
        with pytest.raises(ProviderNotApprovedError):
            await CohereGateway().embed(["PUBLIC SYNTHETIC"])
