from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import settings
from src.processing.embedding.dense_embedder import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.models.search import HybridSearchResult
from src.processing.models.sparse_vector import SparseVector
from src.processing.sparse_embedding.bm25_embedder import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.retrieval import (
    HybridQueryValidationError,
    HybridRetrievalError,
    HybridRetriever,
)
from src.vector_store import QdrantVectorStore, QdrantVectorStoreError


def _create_sample_hybrid_result(
    point_id: str = "point-1",
    score: float = 0.85,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Test chunk content",
    rank: int = 1,
) -> HybridSearchResult:
    return HybridSearchResult(
        point_id=point_id,
        score=score,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        token_count=len(content.split()),
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rank=rank,
    )


# ==============================================================================
# 1. Successful Hybrid Search & Orchestration
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_hybrid_search() -> None:
    """
    Given a valid user query:
    - dense query vector is generated
    - sparse BM25 query vector is generated
    - vector_store.search_hybrid is called once with both vectors, top_k, candidate_k
    - fused results and their ordering are preserved
    """
    mock_dense = MagicMock(spec=DenseEmbedder)
    dense_vec = [0.1] * 384
    mock_dense.embed_texts.return_value = [dense_vec]

    mock_sparse = MagicMock(spec=BM25Embedder)
    sparse_vec = SparseVector(indices=[10, 20], values=[1.5, 2.5])
    mock_sparse.embed_query.return_value = sparse_vec

    mock_store = AsyncMock(spec=QdrantVectorStore)
    expected_results = [
        _create_sample_hybrid_result(point_id="p1", score=0.033, rank=1),
        _create_sample_hybrid_result(point_id="p2", score=0.016, rank=2),
    ]
    mock_store.search_hybrid.return_value = expected_results

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
        default_top_k=10,
        default_candidate_k=20,
    )

    query = "How does RabbitMQ handle background processing?"
    results = await retriever.search(query)

    # 1. Dense embedder called once with trimmed query list
    mock_dense.embed_texts.assert_called_once_with([query])

    # 2. Sparse embedder called once with trimmed query string
    mock_sparse.embed_query.assert_called_once_with(query)

    # 3. Vector store search_hybrid called once with expected arguments
    mock_store.search_hybrid.assert_awaited_once_with(
        dense_query_vector=dense_vec,
        sparse_query_vector=sparse_vec,
        limit=10,
        candidate_limit=20,
        document_id=None,
    )

    # 4. Results returned and ordering preserved
    assert results == expected_results
    assert len(results) == 2
    assert results[0].score == 0.033
    assert results[1].score == 0.016


# ==============================================================================
# 2. Query Validation & Trimming
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_query_raises_validation_error() -> None:
    """Empty query must raise HybridQueryValidationError without invoking backends."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    with pytest.raises(HybridQueryValidationError, match="non-empty"):
        await retriever.search("")

    mock_dense.embed_texts.assert_not_called()
    mock_sparse.embed_query.assert_not_called()
    mock_store.search_hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitespace_only_query_raises_validation_error() -> None:
    """Whitespace query must raise HybridQueryValidationError and abort."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    with pytest.raises(HybridQueryValidationError, match="non-empty"):
        await retriever.search("   \t\n   ")

    mock_dense.embed_texts.assert_not_called()
    mock_sparse.embed_query.assert_not_called()
    mock_store.search_hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_trimming_preserves_internal_terms() -> None:
    """Outer whitespace is trimmed while internal whitespace and terms are preserved."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    raw_query = "   distributed  vector search   "
    await retriever.search(raw_query)

    expected_clean = "distributed  vector search"
    mock_dense.embed_texts.assert_called_once_with([expected_clean])
    mock_sparse.embed_query.assert_called_once_with(expected_clean)


# ==============================================================================
# 3. Top-K Configuration & Validation
# ==============================================================================


@pytest.mark.asyncio
async def test_default_top_k() -> None:
    """When top_k is None, the configured default HYBRID_SEARCH_TOP_K is passed."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
        default_top_k=settings.HYBRID_SEARCH_TOP_K,
        default_candidate_k=settings.HYBRID_SEARCH_CANDIDATE_K,
    )

    await retriever.search("query", top_k=None)

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["limit"] == settings.HYBRID_SEARCH_TOP_K


@pytest.mark.asyncio
async def test_custom_top_k() -> None:
    """When caller supplies top_k=7, limit receives 7."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
        default_candidate_k=50,
    )

    await retriever.search("query", top_k=7)

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["limit"] == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_k", [0, -1, -10, "5", 3.14])  # type: ignore[arg-type]
async def test_invalid_top_k_raises(invalid_k: int) -> None:
    """Non-positive or non-integer top_k raises HybridQueryValidationError."""
    retriever = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
    )

    with pytest.raises(HybridQueryValidationError, match="positive integer"):
        await retriever.search("query", top_k=invalid_k)


@pytest.mark.asyncio
async def test_top_k_exceeding_max_raises() -> None:
    """top_k exceeding max_top_k raises HybridQueryValidationError."""
    retriever = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
        max_top_k=100,
    )

    with pytest.raises(HybridQueryValidationError, match="exceeds maximum"):
        await retriever.search("query", top_k=101)


# ==============================================================================
# 4. Candidate-K Configuration & Validation
# ==============================================================================


@pytest.mark.asyncio
async def test_default_candidate_k() -> None:
    """When candidate_k is None, default_candidate_k is passed to search_hybrid."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
        default_top_k=10,
        default_candidate_k=25,
    )

    await retriever.search("query", candidate_k=None)

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["candidate_limit"] == 25


@pytest.mark.asyncio
async def test_custom_candidate_k() -> None:
    """When candidate_k is explicitly supplied, it is forwarded correctly."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    await retriever.search("query", top_k=10, candidate_k=30)

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["candidate_limit"] == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_cand_k", [0, -1, -50, "20", 15.5])  # type: ignore[arg-type]
async def test_invalid_candidate_k_raises(invalid_cand_k: int) -> None:
    """Non-positive or non-integer candidate_k raises HybridQueryValidationError."""
    retriever = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
    )

    with pytest.raises(HybridQueryValidationError, match="positive integer"):
        await retriever.search("query", candidate_k=invalid_cand_k)


@pytest.mark.asyncio
async def test_candidate_k_smaller_than_top_k_raises() -> None:
    """candidate_k < top_k raises HybridQueryValidationError."""
    retriever = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
    )

    with pytest.raises(HybridQueryValidationError, match="cannot be less than top_k"):
        await retriever.search("query", top_k=20, candidate_k=10)


# ==============================================================================
# 5. Document ID Filtering
# ==============================================================================


@pytest.mark.asyncio
async def test_document_id_filter_forwarded() -> None:
    """document_id parameter is forwarded to vector store search_hybrid."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    await retriever.search("query", document_id="doc-hybrid-999")

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["document_id"] == "doc-hybrid-999"


@pytest.mark.asyncio
async def test_no_document_filter_uses_global_search() -> None:
    """When document_id is None, document_id=None is forwarded to vector store."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    await retriever.search("query", document_id=None)

    _, kwargs = mock_store.search_hybrid.call_args
    assert kwargs["document_id"] is None


@pytest.mark.asyncio
async def test_empty_whitespace_document_id_raises() -> None:
    """Whitespace document_id raises HybridQueryValidationError."""
    retriever = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
    )

    with pytest.raises(HybridQueryValidationError, match="non-empty string"):
        await retriever.search("query", document_id="   ")


# ==============================================================================
# 6. Embedding Failure Handling & Resilience
# ==============================================================================


@pytest.mark.asyncio
async def test_dense_embedding_failure_aborts_before_search() -> None:
    """DenseEmbeddingError wraps into HybridRetrievalError and stops pipeline."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.side_effect = DenseEmbeddingError(
        "Torch model out of memory"
    )
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    with pytest.raises(HybridRetrievalError, match="dense query embedding"):
        await retriever.search("query")

    mock_sparse.embed_query.assert_not_called()
    mock_store.search_hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_sparse_embedding_failure_aborts_before_search() -> None:
    """SparseEmbeddingError wraps into HybridRetrievalError and stops search."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.side_effect = SparseEmbeddingError("FastEmbed failure")
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    with pytest.raises(HybridRetrievalError, match="sparse query embedding"):
        await retriever.search("query")

    mock_store.search_hybrid.assert_not_awaited()


@pytest.mark.asyncio
async def test_sparse_empty_vector_executes_hybrid_search() -> None:
    """
    When sparse embedding produces an empty vector (e.g. stopwords only),
    search_hybrid is still invoked with the empty SparseVector so dense
    matches are retrieved.
    """
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    empty_sv = SparseVector(indices=[], values=[])
    mock_sparse.embed_query.return_value = empty_sv
    mock_store = AsyncMock(spec=QdrantVectorStore)
    expected = [_create_sample_hybrid_result(point_id="p1", score=0.5)]
    mock_store.search_hybrid.return_value = expected

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    results = await retriever.search("the a")

    mock_store.search_hybrid.assert_awaited_once_with(
        dense_query_vector=[0.1] * 384,
        sparse_query_vector=empty_sv,
        limit=10,
        candidate_limit=20,
        document_id=None,
    )
    assert results == expected


# ==============================================================================
# 7. Qdrant Failure & Result Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_qdrant_failure_wrapped_into_hybrid_retrieval_error() -> None:
    """QdrantVectorStoreError wraps into HybridRetrievalError."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.side_effect = QdrantVectorStoreError("Connection refused")

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    with pytest.raises(HybridRetrievalError, match="Hybrid vector search failed"):
        await retriever.search("query")


@pytest.mark.asyncio
async def test_empty_qdrant_results_returns_empty_list() -> None:
    """When Qdrant returns no points, retriever returns [] cleanly."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_hybrid.return_value = []

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    results = await retriever.search("query")
    assert results == []
    assert isinstance(results, list)


@pytest.mark.asyncio
async def test_result_ordering_preserved() -> None:
    """Fused Qdrant result order is preserved exactly."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)

    scored_results = [
        _create_sample_hybrid_result(point_id="p-alpha", score=0.033, rank=1),
        _create_sample_hybrid_result(point_id="p-beta", score=0.025, rank=2),
        _create_sample_hybrid_result(point_id="p-gamma", score=0.016, rank=3),
    ]
    mock_store.search_hybrid.return_value = scored_results

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    results = await retriever.search("query")
    assert [r.point_id for r in results] == ["p-alpha", "p-beta", "p-gamma"]
    assert [r.score for r in results] == [0.033, 0.025, 0.016]


@pytest.mark.asyncio
async def test_rank_preservation() -> None:
    """Ensures ranks are 1-indexed hybrid fused ranks."""
    mock_dense = MagicMock(spec=DenseEmbedder)
    mock_dense.embed_texts.return_value = [[0.1] * 384]
    mock_sparse = MagicMock(spec=BM25Embedder)
    mock_sparse.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)

    scored_results = [
        _create_sample_hybrid_result(point_id="p1", rank=1),
        _create_sample_hybrid_result(point_id="p2", rank=2),
    ]
    mock_store.search_hybrid.return_value = scored_results

    retriever = HybridRetriever(
        dense_embedder=mock_dense,
        sparse_embedder=mock_sparse,
        vector_store=mock_store,
    )

    results = await retriever.search("query")
    assert results[0].rank == 1
    assert results[1].rank == 2
