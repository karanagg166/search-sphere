from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config import settings
from src.processing.embedding.dense_embedder import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.models.search import DenseSearchResult
from src.retrieval import (
    DenseRetrievalError,
    DenseRetriever,
    QueryValidationError,
)
from src.vector_store import QdrantVectorStore, QdrantVectorStoreError


def _create_sample_result(
    point_id: str = "point-1",
    score: float = 0.95,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Test chunk content",
    rank: int = 1,
) -> DenseSearchResult:
    return DenseSearchResult(
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
# 1. Successful Search & Basic Orchestration
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_search() -> None:
    """
    Given a valid user query, embedder produces 1 vector and vector store returns
    ordered search results.
    """
    mock_embedder = MagicMock(spec=DenseEmbedder)
    query_vec = [0.1] * 384
    mock_embedder.embed_texts.return_value = [query_vec]

    mock_store = AsyncMock(spec=QdrantVectorStore)
    expected_results = [
        _create_sample_result(point_id="p1", score=0.92, rank=1),
        _create_sample_result(point_id="p2", score=0.85, rank=2),
    ]
    mock_store.search_dense.return_value = expected_results

    retriever = DenseRetriever(
        embedder=mock_embedder,
        vector_store=mock_store,
        default_top_k=10,
    )

    query = "How does the worker retrieve the PDF?"
    results = await retriever.search(query)

    # 1. Query embedded exactly once with original query text
    mock_embedder.embed_texts.assert_called_once_with([query])

    # 2. Vector store search called once with generated vector and limit
    mock_store.search_dense.assert_awaited_once_with(
        query_vector=query_vec,
        limit=10,
        document_id=None,
        score_threshold=None,
    )

    # 3. Results returned in same order
    assert results == expected_results
    assert len(results) == 2
    assert results[0].score == 0.92
    assert results[1].score == 0.85


# ==============================================================================
# 2. Query Validation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_query_raises_validation_error() -> None:
    """Empty string query must raise QueryValidationError and abort pipeline."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(QueryValidationError, match="non-empty"):
        await retriever.search("")

    mock_embedder.embed_texts.assert_not_called()
    mock_store.search_dense.assert_not_awaited()


@pytest.mark.asyncio
async def test_whitespace_only_query_raises_validation_error() -> None:
    """Whitespace-only query must raise QueryValidationError and abort pipeline."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(QueryValidationError, match="non-empty"):
        await retriever.search("   \n\t  ")

    mock_embedder.embed_texts.assert_not_called()
    mock_store.search_dense.assert_not_awaited()


@pytest.mark.asyncio
async def test_query_trimming() -> None:
    """
    Leading and trailing whitespace is stripped while internal terms are preserved.
    """
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    raw_query = "   rabbitmq retry behavior   "
    await retriever.search(raw_query)

    # Meaningful trimmed query must be embedded without altering internal spacing
    mock_embedder.embed_texts.assert_called_once_with(["rabbitmq retry behavior"])


# ==============================================================================
# 3. Top-K Configuration & Validation
# ==============================================================================


@pytest.mark.asyncio
async def test_default_top_k() -> None:
    """When top_k is None, the configured default DENSE_SEARCH_TOP_K is passed."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(
        embedder=mock_embedder,
        vector_store=mock_store,
        default_top_k=settings.DENSE_SEARCH_TOP_K,
    )

    await retriever.search("sample query", top_k=None)

    _, kwargs = mock_store.search_dense.call_args
    assert kwargs["limit"] == settings.DENSE_SEARCH_TOP_K


@pytest.mark.asyncio
async def test_custom_top_k() -> None:
    """When caller supplies top_k=5, Qdrant limit receives 5."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    await retriever.search("sample query", top_k=5)

    _, kwargs = mock_store.search_dense.call_args
    assert kwargs["limit"] == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_k", [0, -1, -50])
async def test_invalid_top_k_non_positive_raises(invalid_k: int) -> None:
    """Non-positive top_k raises QueryValidationError."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(QueryValidationError, match="positive integer"):
        await retriever.search("sample query", top_k=invalid_k)

    mock_embedder.embed_texts.assert_not_called()
    mock_store.search_dense.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_top_k_exceeding_max_raises() -> None:
    """top_k exceeding max_top_k raises QueryValidationError."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(
        embedder=mock_embedder,
        vector_store=mock_store,
        max_top_k=100,
    )

    with pytest.raises(QueryValidationError, match="exceeds maximum"):
        await retriever.search("sample query", top_k=101)


# ==============================================================================
# 4. Document ID Filtering
# ==============================================================================


@pytest.mark.asyncio
async def test_document_id_filtering() -> None:
    """document_id parameter is forwarded to vector store for server-side filtering."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    await retriever.search("sample query", document_id="doc-123")

    _, kwargs = mock_store.search_dense.call_args
    assert kwargs["document_id"] == "doc-123"


@pytest.mark.asyncio
async def test_no_document_filter() -> None:
    """When document_id is None, unfiltered search is performed."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    await retriever.search("sample query", document_id=None)

    _, kwargs = mock_store.search_dense.call_args
    assert kwargs["document_id"] is None


@pytest.mark.asyncio
async def test_empty_whitespace_document_id_raises() -> None:
    """Whitespace-only document_id raises QueryValidationError."""
    retriever = DenseRetriever(embedder=MagicMock(), vector_store=AsyncMock())

    with pytest.raises(QueryValidationError, match="non-empty string"):
        await retriever.search("sample query", document_id="   ")


# ==============================================================================
# 5. Empty Results Handling
# ==============================================================================


@pytest.mark.asyncio
async def test_empty_results_returns_empty_list() -> None:
    """When Qdrant returns no hits, empty list is returned without raising."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    results = await retriever.search("unmatched obscure query")

    assert results == []
    assert isinstance(results, list)


# ==============================================================================
# 6. Error Wrapping & Resilience
# ==============================================================================


@pytest.mark.asyncio
async def test_query_embedding_failure_raises_dense_retrieval_error() -> None:
    """DenseEmbeddingError during query embedding wraps into DenseRetrievalError."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.side_effect = DenseEmbeddingError(
        "Failed to load sentence-transformer model"
    )
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(DenseRetrievalError, match="Failed to generate query embedding"):
        await retriever.search("sample query")

    # Qdrant search must not run if embedding fails
    mock_store.search_dense.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_store_failure_raises_dense_retrieval_error() -> None:
    """QdrantVectorStoreError during ANN search wraps into DenseRetrievalError."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.side_effect = QdrantVectorStoreError(
        "Qdrant server unavailable"
    )

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(DenseRetrievalError, match="Dense vector search failed"):
        await retriever.search("sample query")


@pytest.mark.asyncio
async def test_unexpected_embedder_vector_count_raises() -> None:
    """Embedder returning 0 or >1 vectors for 1 query raises DenseRetrievalError."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = []  # Empty vectors
    mock_store = AsyncMock(spec=QdrantVectorStore)

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    with pytest.raises(DenseRetrievalError, match="Expected exactly 1 query vector"):
        await retriever.search("sample query")


# ==============================================================================
# 7. Result Ordering & Score Preservation
# ==============================================================================


@pytest.mark.asyncio
async def test_result_ordering_preserved() -> None:
    """Results returned by Qdrant in order [0.91, 0.87, 0.72] maintain exact order."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]

    scored_results = [
        _create_sample_result(point_id="p1", score=0.91, rank=1),
        _create_sample_result(point_id="p2", score=0.87, rank=2),
        _create_sample_result(point_id="p3", score=0.72, rank=3),
    ]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = scored_results

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    results = await retriever.search("test query")

    assert [r.score for r in results] == [0.91, 0.87, 0.72]
    assert [r.point_id for r in results] == ["p1", "p2", "p3"]


@pytest.mark.asyncio
async def test_score_preservation_exact() -> None:
    """Score equals exact float from Qdrant without conversion to percentages."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]

    exact_score = 0.897451239
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = [
        _create_sample_result(score=exact_score),
    ]

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    results = await retriever.search("test query")

    assert len(results) == 1
    assert results[0].score == exact_score
    assert isinstance(results[0].score, float)


@pytest.mark.asyncio
async def test_score_threshold_passthrough() -> None:
    """Optional score_threshold parameter is forwarded to vector store."""
    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_texts.return_value = [[0.1] * 384]
    mock_store = AsyncMock(spec=QdrantVectorStore)
    mock_store.search_dense.return_value = []

    retriever = DenseRetriever(embedder=mock_embedder, vector_store=mock_store)

    await retriever.search("test query", score_threshold=0.75)

    _, kwargs = mock_store.search_dense.call_args
    assert kwargs["score_threshold"] == 0.75
