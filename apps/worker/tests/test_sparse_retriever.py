from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.models.search import SparseSearchResult
from src.processing.models.sparse_vector import SparseVector
from src.processing.sparse_embedding.bm25_embedder import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.retrieval.sparse_retriever import (
    SparseQueryValidationError,
    SparseRetrievalError,
    SparseRetriever,
)
from src.vector_store.qdrant_store import (
    QdrantVectorStore,
    QdrantVectorStoreError,
)


def _create_sample_sparse_result(
    point_id: str = "p-1",
    score: float = 2.45,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Error code ERR_CONNECTION_RESET occurs when TCP is reset.",
    rank: int = 1,
) -> SparseSearchResult:
    return SparseSearchResult(
        point_id=point_id,
        score=score,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        token_count=10,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rank=rank,
    )


# ==============================================================================
# 22. Successful Search
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_search() -> None:
    """Verify flow: validation -> embed -> qdrant search -> ordered results."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(
        indices=[101, 202], values=[1.0, 1.0]
    )

    mock_store = AsyncMock(spec=QdrantVectorStore)
    expected_result = _create_sample_sparse_result(score=3.14, rank=1)
    mock_store.search_sparse.return_value = [expected_result]

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    results = await retriever.search("ERR_CONNECTION_RESET")

    assert len(results) == 1
    assert results[0] == expected_result
    assert results[0].score == 3.14
    assert results[0].rank == 1

    mock_embedder.embed_query.assert_called_once_with("ERR_CONNECTION_RESET")
    mock_store.search_sparse.assert_called_once_with(
        query_vector=SparseVector(indices=[101, 202], values=[1.0, 1.0]),
        limit=10,
        document_id=None,
        score_threshold=None,
    )


# ==============================================================================
# 23 & 24. Query Validation (Empty / Whitespace)
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_query_raises_validation_error() -> None:
    """Empty query string must raise validation error without calling embedder."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)
    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(SparseQueryValidationError, match="non-empty, non-whitespace"):
        await retriever.search("")

    mock_embedder.embed_query.assert_not_called()
    mock_store.search_sparse.assert_not_called()


@pytest.mark.asyncio
async def test_whitespace_query_raises_validation_error() -> None:
    """Whitespace-only query string must raise SparseQueryValidationError."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)
    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(SparseQueryValidationError, match="non-empty, non-whitespace"):
        await retriever.search("   \t\n  ")

    mock_embedder.embed_query.assert_not_called()
    mock_store.search_sparse.assert_not_called()


# ==============================================================================
# 25 & 26. top_k Parameters (Default & Custom)
# ==============================================================================


@pytest.mark.asyncio
async def test_default_top_k_used() -> None:
    """When top_k is None, default_top_k (10) is passed to search_sparse."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = []

    retriever = SparseRetriever(
        embedder=mock_embedder,
        vector_store=mock_store,
        default_top_k=10,
    )
    await retriever.search("query")

    mock_store.search_sparse.assert_called_once_with(
        query_vector=SparseVector(indices=[1], values=[1.0]),
        limit=10,
        document_id=None,
        score_threshold=None,
    )


@pytest.mark.asyncio
async def test_custom_top_k_passed() -> None:
    """Explicit valid top_k is passed to search_sparse."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = []

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    await retriever.search("query", top_k=25)

    mock_store.search_sparse.assert_called_once_with(
        query_vector=SparseVector(indices=[1], values=[1.0]),
        limit=25,
        document_id=None,
        score_threshold=None,
    )


# ==============================================================================
# 27. Invalid top_k Rejection
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_k", [0, -1, -50, "five"])
async def test_invalid_top_k_rejected(invalid_k: int) -> None:
    """Zero, negative, or non-integer top_k values raise SparseQueryValidationError."""
    retriever = SparseRetriever()
    with pytest.raises(SparseQueryValidationError, match="positive integer"):
        await retriever.search("valid query", top_k=invalid_k)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_top_k_exceeding_max_rejected() -> None:
    """top_k exceeding max_top_k raises SparseQueryValidationError."""
    retriever = SparseRetriever(max_top_k=50)
    with pytest.raises(SparseQueryValidationError, match="exceeds maximum allowed"):
        await retriever.search("valid query", top_k=51)


# ==============================================================================
# 28 & 29. document_id Filter Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_document_id_filter_passed_to_qdrant() -> None:
    """Provided document_id filter is passed to QdrantVectorStore.search_sparse."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = []

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    await retriever.search("query", document_id="doc-xyz-789")

    mock_store.search_sparse.assert_called_once_with(
        query_vector=SparseVector(indices=[1], values=[1.0]),
        limit=10,
        document_id="doc-xyz-789",
        score_threshold=None,
    )


@pytest.mark.asyncio
async def test_no_filter_passes_none() -> None:
    """Omitting document_id passes None to search_sparse."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = []

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    await retriever.search("query", document_id=None)

    assert mock_store.search_sparse.call_args.kwargs["document_id"] is None


@pytest.mark.asyncio
async def test_invalid_document_id_rejected() -> None:
    """Empty or whitespace-only document_id raises SparseQueryValidationError."""
    retriever = SparseRetriever()
    with pytest.raises(SparseQueryValidationError, match="non-empty string"):
        await retriever.search("valid query", document_id="   ")


# ==============================================================================
# 30. Empty Result List
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_result_list_returned() -> None:
    """When vector store finds no matches, returns empty list."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = []

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    results = await retriever.search("unmatched keyword")

    assert results == []


# ==============================================================================
# 31. Sparse Embedding Failure
# ==============================================================================


@pytest.mark.asyncio
async def test_sparse_embedding_failure_does_not_call_qdrant() -> None:
    """If BM25Embedder raises error, Qdrant is not called, and error is raised."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.side_effect = SparseEmbeddingError("Tokenization error")
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(
        SparseRetrievalError, match="Failed to generate sparse query embedding"
    ):
        await retriever.search("query that fails embedding")

    mock_store.search_sparse.assert_not_called()


# ==============================================================================
# 32. Qdrant Failure Wrapping
# ==============================================================================


@pytest.mark.asyncio
async def test_qdrant_failure_wrapped_in_sparse_retrieval_error() -> None:
    """Qdrant exceptions are wrapped into SparseRetrievalError."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.side_effect = QdrantVectorStoreError("Connection timeout")

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(
        SparseRetrievalError, match="Sparse vector search execution failed"
    ):
        await retriever.search("valid query")


# ==============================================================================
# 33 & 34. Result Order & Raw Score Preserved
# ==============================================================================


@pytest.mark.asyncio
async def test_result_order_and_raw_score_preserved() -> None:
    """Ranking order and raw BM25 scores from Qdrant are preserved intact."""
    mock_embedder = MagicMock(spec=BM25Embedder)
    mock_embedder.embed_query.return_value = SparseVector(indices=[1], values=[1.0])

    r1 = _create_sample_sparse_result(point_id="p-1", score=5.67, rank=1)
    r2 = _create_sample_sparse_result(point_id="p-2", score=2.34, rank=2)
    r3 = _create_sample_sparse_result(point_id="p-3", score=0.89, rank=3)

    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_sparse.return_value = [r1, r2, r3]

    retriever = SparseRetriever(embedder=mock_embedder, vector_store=mock_store)
    results = await retriever.search("query")

    assert len(results) == 3
    assert [r.point_id for r in results] == ["p-1", "p-2", "p-3"]
    assert [r.score for r in results] == [5.67, 2.34, 0.89]
    assert [r.rank for r in results] == [1, 2, 3]
