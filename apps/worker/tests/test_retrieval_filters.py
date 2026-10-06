from unittest.mock import AsyncMock, MagicMock
import pytest
from qdrant_client import models

from src.processing.models.search import (
    DenseSearchResult,
    HybridSearchResult,
    SparseSearchResult,
)
from src.processing.models.sparse_vector import SparseVector
from src.retrieval.dense_retriever import DenseRetriever
from src.retrieval.sparse_retriever import SparseRetriever
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.retrieval.reranker import CrossEncoderReranker
from src.vector_store.qdrant_store import QdrantVectorStore


@pytest.mark.asyncio
async def test_dense_retriever_forwards_filters():
    mock_embedder = MagicMock()
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_vector_store = AsyncMock(spec=QdrantVectorStore)
    mock_vector_store.search_dense.return_value = [
        DenseSearchResult(
            point_id="p1",
            score=0.95,
            document_id="doc1",
            chunk_index=0,
            content="BP 120/80",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            patient_id="pat-A",
            source_system="quick_clinic",
        )
    ]

    retriever = DenseRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
    )

    filters = {"source_system": "quick_clinic", "patient_id": "pat-A"}
    results = await retriever.search(
        query="blood pressure",
        top_k=5,
        filters=filters,
    )

    assert len(results) == 1
    assert results[0].patient_id == "pat-A"
    mock_vector_store.search_dense.assert_awaited_once_with(
        query_vector=[0.1] * 384,
        limit=5,
        document_id=None,
        score_threshold=None,
        filters=filters,
    )


@pytest.mark.asyncio
async def test_sparse_retriever_forwards_filters():
    mock_embedder = MagicMock()
    mock_sparse_vec = SparseVector(indices=[1, 2], values=[0.5, 0.8])
    mock_embedder.embed_query.return_value = mock_sparse_vec
    mock_vector_store = AsyncMock(spec=QdrantVectorStore)
    mock_vector_store.search_sparse.return_value = [
        SparseSearchResult(
            point_id="p1",
            score=2.5,
            document_id="doc1",
            chunk_index=0,
            content="BP 120/80",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            patient_id="pat-A",
            source_system="quick_clinic",
        )
    ]

    retriever = SparseRetriever(
        embedder=mock_embedder,
        vector_store=mock_vector_store,
    )

    filters = {"source_system": "quick_clinic", "patient_id": "pat-A"}
    results = await retriever.search(
        query="blood pressure",
        top_k=5,
        filters=filters,
    )

    assert len(results) == 1
    assert results[0].patient_id == "pat-A"
    mock_vector_store.search_sparse.assert_awaited_once_with(
        query_vector=mock_sparse_vec,
        limit=5,
        document_id=None,
        score_threshold=None,
        filters=filters,
    )


@pytest.mark.asyncio
async def test_hybrid_retriever_forwards_filters_to_both_branches():
    mock_dense = MagicMock()
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock()
    mock_sparse_vec = SparseVector(indices=[10, 20], values=[0.4, 0.9])
    mock_sparse.embed_query.return_value = mock_sparse_vec

    mock_vector_store = AsyncMock(spec=QdrantVectorStore)
    mock_vector_store.search_hybrid.return_value = [
        HybridSearchResult(
            point_id="p1",
            score=0.033,
            document_id="doc1",
            chunk_index=0,
            content="BP 120/80",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            patient_id="pat-A",
            source_system="quick_clinic",
        )
    ]

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_vector_store,
    )

    filters = {"source_system": "quick_clinic", "patient_id": "pat-A"}
    results = await retriever.search(
        query="blood pressure",
        top_k=5,
        candidate_k=10,
        filters=filters,
    )

    assert len(results) == 1
    mock_vector_store.search_hybrid.assert_awaited_once_with(
        dense_query_vector=[0.1] * 384,
        sparse_query_vector=mock_sparse_vec,
        limit=5,
        candidate_limit=10,
        document_id=None,
        filters=filters,
    )


@pytest.mark.asyncio
async def test_qdrant_store_hybrid_mandatory_filters_applied_to_dense_and_sparse():
    """Verify that search_hybrid applies patient_id and source_system filter to BOTH prefetches and outer query."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.points = []
    mock_client.query_points.return_value = mock_response

    store = QdrantVectorStore(
        client=mock_client,
        collection_name="test_collection",
        vector_dimension=4,
    )

    dense_vec = [0.1, 0.2, 0.3, 0.4]
    sparse_vec = SparseVector(indices=[1, 2], values=[0.5, 0.6])
    filters = {"source_system": "quick_clinic", "patient_id": "pat-A"}

    await store.search_hybrid(
        dense_query_vector=dense_vec,
        sparse_query_vector=sparse_vec,
        limit=5,
        candidate_limit=10,
        filters=filters,
    )

    assert mock_client.query_points.called
    call_kwargs = mock_client.query_points.call_args.kwargs
    prefetches = call_kwargs["prefetch"]
    assert len(prefetches) == 2

    dense_prefetch = prefetches[0]
    sparse_prefetch = prefetches[1]
    outer_filter = call_kwargs["query_filter"]

    # Verify both dense and sparse branch filters are present and identical
    for pf in [dense_prefetch, sparse_prefetch]:
        filter_obj = pf.filter
        assert filter_obj is not None
        keys = {cond.key: cond.match.value for cond in filter_obj.must}
        assert keys["source_system"] == "quick_clinic"
        assert keys["patient_id"] == "pat-A"

    assert outer_filter is not None
    outer_keys = {cond.key: cond.match.value for cond in outer_filter.must}
    assert outer_keys["source_system"] == "quick_clinic"
    assert outer_keys["patient_id"] == "pat-A"


@pytest.mark.asyncio
async def test_reranked_hybrid_retriever_safety_and_metadata_preservation():
    """Verify that RerankedHybridRetriever forwards filters to hybrid retrieval and preserves metadata."""
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_hybrid.search.return_value = [
        HybridSearchResult(
            point_id="p1",
            score=0.033,
            document_id="doc1",
            chunk_index=0,
            content="BP 120/80",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            patient_id="pat-A",
            source_system="quick_clinic",
            document_type="LAB_REPORT",
            report_date="2026-10-01T00:00:00Z",
            file_name="bp.pdf",
        )
    ]

    mock_reranker = MagicMock(spec=CrossEncoderReranker)
    # Reranker rerank method returns reranked result
    reranker = CrossEncoderReranker(model=MagicMock())
    reranker.rerank = MagicMock()
    from src.processing.models.search import RerankedSearchResult

    expected_reranked = [
        RerankedSearchResult(
            point_id="p1",
            document_id="doc1",
            chunk_index=0,
            content="BP 120/80",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=0.92,
            rrf_score=0.033,
            score=0.92,
            rank=1,
            patient_id="pat-A",
            source_system="quick_clinic",
            document_type="LAB_REPORT",
            report_date="2026-10-01T00:00:00Z",
            file_name="bp.pdf",
        )
    ]
    reranker.arerank = AsyncMock(return_value=expected_reranked)

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=reranker,
    )

    filters = {"source_system": "quick_clinic", "patient_id": "pat-A"}
    results = await retriever.search(
        query="blood pressure",
        top_k=5,
        candidate_k=10,
        filters=filters,
    )

    assert len(results) == 1
    assert results[0].patient_id == "pat-A"
    assert results[0].document_type == "LAB_REPORT"
    assert results[0].report_date == "2026-10-01T00:00:00Z"
    assert results[0].file_name == "bp.pdf"

    # Verify hybrid stage was called with filters
    mock_hybrid.search.assert_awaited_once_with(
        query="blood pressure",
        top_k=10,
        candidate_k=10,
        document_id=None,
        filters=filters,
    )
