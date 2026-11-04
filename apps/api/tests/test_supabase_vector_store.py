import pytest
from unittest.mock import AsyncMock, patch

from src.processing.models.document import EmbeddedChunk, EmbeddedDocument
from src.processing.models.sparse_vector import SparseVector
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.services.document_indexer import audit_and_recover_missing_vectors
from src.vector_store import get_vector_store
from src.vector_store.supabase_vector_store import SupabaseVectorStore


def sample_embedded_doc(chunks_text: list[str]) -> EmbeddedDocument:
    chunks = []
    for idx, text in enumerate(chunks_text):
        emb = [0.0] * 384
        emb[idx % 384] = 1.0
        chunks.append(
            EmbeddedChunk(
                chunk_index=idx,
                content=text,
                token_count=len(text.split()),
                start_page=1,
                end_page=1,
                page_numbers=[1],
                block_types=["paragraph"],
                embedding=emb,
            )
        )
    return EmbeddedDocument(chunks=chunks)


@pytest.mark.asyncio
async def test_supabase_vector_store_factory():
    """Verify get_vector_store returns SupabaseVectorStore when configured."""
    with patch("src.config.settings.VECTOR_STORE_BACKEND", "supabase"):
        store = get_vector_store()
        assert isinstance(store, SupabaseVectorStore)
        assert store.vector_dimension == 384


@pytest.mark.asyncio
async def test_supabase_vector_store_indexing_and_counting():
    """Verify document chunk indexing, deterministic point_ids, and counting."""
    store = SupabaseVectorStore()
    doc_id = "test-doc-durability-001"
    doc = sample_embedded_doc(["First chunk of text about diabetes.", "Second chunk about hypertension."])

    points_written = await store.index_document(doc_id, doc)
    assert points_written == 2

    count = await store.count_document_points(doc_id)
    assert count == 2

    # Reindexing the same document should clean up previous chunks without duplicates
    updated_doc = sample_embedded_doc(["Updated single chunk about medical cardiology."])
    points_written = await store.index_document(doc_id, updated_doc)
    assert points_written == 1

    count_after = await store.count_document_points(doc_id)
    assert count_after == 1

    # Cleanup
    await store.delete_document_points(doc_id)
    assert await store.count_document_points(doc_id) == 0


@pytest.mark.asyncio
async def test_supabase_vector_store_dense_search():
    """Verify dense vector search returns ordered results preserving cosine distance ranking."""
    store = SupabaseVectorStore()
    doc_id = "test-doc-dense-002"
    doc = sample_embedded_doc(["Chunk A exact match", "Chunk B partial match"])
    await store.index_document(doc_id, doc)

    try:
        # Search using query vector aligned with chunk 0
        query_vec = [0.0] * 384
        query_vec[0] = 1.0
        results = await store.search_dense(query_vector=query_vec, limit=5, document_id=doc_id)
        assert len(results) >= 1
        assert results[0].document_id == doc_id
        assert results[0].chunk_index == 0
        assert results[0].score > 0.99
    finally:
        await store.delete_document_points(doc_id)


@pytest.mark.asyncio
async def test_supabase_vector_store_sparse_search():
    """Verify lexical search matches content keywords."""
    store = SupabaseVectorStore()
    doc_id = "test-doc-sparse-003"
    doc = sample_embedded_doc(["Neurology and brain scan analysis", "Dermatology skin check"])
    await store.index_document(doc_id, doc)

    try:
        sparse_vec = SparseVector(indices=[10, 20], values=[0.5, 0.8])
        results = await store.search_sparse(
            query_vector=sparse_vec,
            limit=5,
            document_id=doc_id,
            query_text="neurology brain",
        )
        assert len(results) >= 1
        assert "Neurology" in results[0].content
    finally:
        await store.delete_document_points(doc_id)


@pytest.mark.asyncio
async def test_supabase_vector_store_hybrid_rrf_search():
    """Verify hybrid search correctly combines dense and sparse rankings via Reciprocal Rank Fusion."""
    store = SupabaseVectorStore()
    doc_id = "test-doc-hybrid-004"
    doc = sample_embedded_doc([
        "Cardiac arrest emergency care protocol",
        "Routine pediatric vaccination schedule",
        "Orthopedic fracture surgery recovery",
    ])
    await store.index_document(doc_id, doc)

    try:
        dense_vec = [0.0] * 384
        dense_vec[0] = 1.0
        sparse_vec = SparseVector(indices=[1, 2], values=[0.5, 0.5])
        results = await store.search_hybrid(
            dense_query_vector=dense_vec,
            sparse_query_vector=sparse_vec,
            limit=2,
            candidate_limit=5,
            document_id=doc_id,
            query_text="cardiac emergency protocol",
        )
        assert len(results) == 2
        assert results[0].rank == 1
        assert results[0].score > 0.0
        assert "Cardiac" in results[0].content
    finally:
        await store.delete_document_points(doc_id)


@pytest.mark.asyncio
async def test_supabase_vector_store_tenant_isolation():
    """Verify tenant and client isolation guarantees prevent cross-tenant data leakage."""
    store = SupabaseVectorStore()
    doc_id = "shared-external-id"
    doc_tenant_a = sample_embedded_doc(["Patient data for Tenant A"])
    doc_tenant_b = sample_embedded_doc(["Patient data for Tenant B"])

    scope_a = {"client_id": "clinic_a", "tenant_id": "tenant_1", "patient_id": "pat_001"}
    scope_b = {"client_id": "clinic_b", "tenant_id": "tenant_2", "patient_id": "pat_002"}

    await store.index_document(doc_id, doc_tenant_a, extra_payload=scope_a)
    await store.index_document(doc_id, doc_tenant_b, extra_payload=scope_b)

    try:
        # Search scoped to Tenant A should only return Tenant A's chunk
        query_vec = [0.0] * 384
        query_vec[0] = 1.0
        results_a = await store.search_dense(
            query_vector=query_vec,
            limit=5,
            document_id=doc_id,
            filters=scope_a,
        )
        assert len(results_a) == 1
        assert results_a[0].client_id == "clinic_a"
        assert "Tenant A" in results_a[0].content

        # Deleting Tenant A chunks should not affect Tenant B
        await store.delete_document_points(doc_id, filters=scope_a)
        assert await store.count_document_points(doc_id, filters=scope_a) == 0
        assert await store.count_document_points(doc_id, filters=scope_b) == 1
    finally:
        await store.delete_document_points(doc_id, filters=scope_b)


@pytest.mark.asyncio
async def test_hybrid_retriever_end_to_end_with_supabase():
    """Verify HybridRetriever seamlessly works with SupabaseVectorStore."""
    store = SupabaseVectorStore()
    doc_id = "test-doc-retriever-005"
    doc = sample_embedded_doc(["Microservices architecture and distributed transactions."])
    await store.index_document(doc_id, doc)

    try:
        retriever = HybridRetriever(vector_store=store)
        results = await retriever.search(
            query="distributed transactions architecture",
            top_k=3,
            candidate_k=5,
            document_id=doc_id,
        )
        assert len(results) == 1
        assert results[0].document_id == doc_id
        assert "Microservices" in results[0].content
    finally:
        await store.delete_document_points(doc_id)


@pytest.mark.asyncio
async def test_missing_vector_recovery_with_supabase_store():
    """Verify audit_and_recover_missing_vectors audits successfully against SupabaseVectorStore."""
    with patch("src.config.settings.VECTOR_STORE_BACKEND", "supabase"), \
         patch("src.services.durable_processor.durable_processor.process_job", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = True
        res = await audit_and_recover_missing_vectors(batch_size=2)
        assert "status" in res
        assert res["status"] in ("ok", "completed", "skipped")
