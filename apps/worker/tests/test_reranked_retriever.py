from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.models.search import HybridSearchResult, RerankedSearchResult
from src.retrieval import (
    CrossEncoderReranker,
    HybridRetrievalError,
    HybridRetriever,
    RerankedHybridRetriever,
    RerankedQueryValidationError,
    RerankedRetrievalError,
    RerankingError,
)


def _create_sample_hybrid_result(
    point_id: str = "point-1",
    score: float = 0.033,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Sample candidate chunk content",
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
# 1. Successful Two-Stage Retrieval Orchestration
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_two_stage_retrieval() -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_reranker = MagicMock(spec=CrossEncoderReranker)

    candidates = [
        _create_sample_hybrid_result(
            point_id="p1", score=0.033, content="Doc 1", rank=1
        ),
        _create_sample_hybrid_result(
            point_id="p2", score=0.016, content="Doc 2", rank=2
        ),
    ]
    mock_hybrid.search.return_value = candidates

    expected_reranked = [
        RerankedSearchResult(
            point_id="p2",
            document_id="doc-1",
            chunk_index=0,
            content="Doc 2",
            token_count=2,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=0.95,
            rrf_score=0.016,
            score=0.95,
            rank=1,
        ),
        RerankedSearchResult(
            point_id="p1",
            document_id="doc-1",
            chunk_index=0,
            content="Doc 1",
            token_count=2,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=0.42,
            rrf_score=0.033,
            score=0.42,
            rank=2,
        ),
    ]
    mock_reranker.arerank = AsyncMock(return_value=expected_reranked)

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
        default_top_k=2,
        default_candidate_k=10,
    )

    query = "  redis eviction strategies  "
    results = await retriever.search(query, top_k=2, candidate_k=10)

    # 1. Hybrid search called with trimmed query and candidate_k
    mock_hybrid.search.assert_awaited_once_with(
        query="redis eviction strategies",
        top_k=10,
        candidate_k=10,
        document_id=None,
    )

    # 2. Reranker called with trimmed query, candidates, and top_k
    mock_reranker.arerank.assert_awaited_once_with(
        query="redis eviction strategies",
        candidates=candidates,
        top_k=2,
    )

    # 3. Final ranked results preserved
    assert results == expected_reranked
    assert len(results) == 2
    assert results[0].point_id == "p2"
    assert results[0].rerank_score == 0.95
    assert results[0].rrf_score == 0.016
    assert results[1].point_id == "p1"
    assert results[1].rerank_score == 0.42
    assert results[1].rrf_score == 0.033


# ==============================================================================
# 2. Validation in RerankedHybridRetriever
# ==============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_query", ["", "   \t\n   ", None, 123])
async def test_invalid_query_raises_validation_error(invalid_query: object) -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_reranker = MagicMock(spec=CrossEncoderReranker)

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
    )

    with pytest.raises(RerankedQueryValidationError, match="non-empty"):
        await retriever.search(invalid_query)  # type: ignore[arg-type]

    mock_hybrid.search.assert_not_awaited()
    mock_reranker.arerank.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_k", [0, -1, "5", 3.14, True, False])
async def test_invalid_top_k_raises_validation_error(invalid_k: object) -> None:
    retriever = RerankedHybridRetriever(
        hybrid_retriever=AsyncMock(spec=HybridRetriever),
        reranker=MagicMock(spec=CrossEncoderReranker),
    )

    with pytest.raises(RerankedQueryValidationError, match="positive integer"):
        await retriever.search("valid query", top_k=invalid_k)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_top_k_exceeding_max_raises_validation_error() -> None:
    retriever = RerankedHybridRetriever(
        hybrid_retriever=AsyncMock(spec=HybridRetriever),
        reranker=MagicMock(spec=CrossEncoderReranker),
        max_top_k=25,
    )

    with pytest.raises(RerankedQueryValidationError, match="exceeds maximum"):
        await retriever.search("valid query", top_k=26)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_cand_k", [0, -1, "10", 4.5, True, False])
async def test_invalid_candidate_k_raises_validation_error(
    invalid_cand_k: object,
) -> None:
    retriever = RerankedHybridRetriever(
        hybrid_retriever=AsyncMock(spec=HybridRetriever),
        reranker=MagicMock(spec=CrossEncoderReranker),
    )

    with pytest.raises(RerankedQueryValidationError, match="positive integer"):
        await retriever.search("valid query", candidate_k=invalid_cand_k)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_candidate_k_smaller_than_top_k_raises_validation_error() -> None:
    retriever = RerankedHybridRetriever(
        hybrid_retriever=AsyncMock(spec=HybridRetriever),
        reranker=MagicMock(spec=CrossEncoderReranker),
    )

    with pytest.raises(RerankedQueryValidationError, match="cannot be less than top_k"):
        await retriever.search("valid query", top_k=10, candidate_k=5)


# ==============================================================================
# 3. Document ID Filtering
# ==============================================================================


@pytest.mark.asyncio
async def test_document_id_filter_forwarded_to_hybrid() -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_hybrid.search.return_value = []
    mock_reranker = MagicMock(spec=CrossEncoderReranker)

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
    )

    await retriever.search("query", document_id="doc-filter-123")

    _, kwargs = mock_hybrid.search.call_args
    assert kwargs["document_id"] == "doc-filter-123"


@pytest.mark.asyncio
async def test_empty_document_id_raises_validation_error() -> None:
    retriever = RerankedHybridRetriever(
        hybrid_retriever=AsyncMock(spec=HybridRetriever),
        reranker=MagicMock(spec=CrossEncoderReranker),
    )

    with pytest.raises(RerankedQueryValidationError, match="non-empty string"):
        await retriever.search("query", document_id="   ")


# ==============================================================================
# 4. Zero Candidates & Error Propagation
# ==============================================================================


@pytest.mark.asyncio
async def test_zero_hybrid_candidates_returns_empty_list_without_reranker() -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_hybrid.search.return_value = []
    mock_reranker = MagicMock(spec=CrossEncoderReranker)
    mock_reranker.arerank = AsyncMock()

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
    )

    results = await retriever.search("query")

    assert results == []
    mock_hybrid.search.assert_awaited_once()
    mock_reranker.arerank.assert_not_called()


@pytest.mark.asyncio
async def test_hybrid_retrieval_error_wrapped() -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_hybrid.search.side_effect = HybridRetrievalError("Qdrant connection failure")
    mock_reranker = MagicMock(spec=CrossEncoderReranker)

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
    )

    with pytest.raises(RerankedRetrievalError, match="Hybrid retrieval stage failed"):
        await retriever.search("query")


@pytest.mark.asyncio
async def test_reranking_error_wrapped() -> None:
    mock_hybrid = AsyncMock(spec=HybridRetriever)
    mock_hybrid.search.return_value = [_create_sample_hybrid_result()]
    mock_reranker = MagicMock(spec=CrossEncoderReranker)
    mock_reranker.arerank = AsyncMock(side_effect=RerankingError("PyTorch OOM"))

    retriever = RerankedHybridRetriever(
        hybrid_retriever=mock_hybrid,
        reranker=mock_reranker,
    )

    with pytest.raises(
        RerankedRetrievalError, match="Cross-encoder reranking stage failed"
    ):
        await retriever.search("query")


# ==============================================================================
# 5. HybridRetriever Convenience Method (search_and_rerank)
# ==============================================================================


@pytest.mark.asyncio
async def test_hybrid_retriever_search_and_rerank() -> None:
    hybrid = HybridRetriever(
        dense_embedder=MagicMock(),
        sparse_embedder=MagicMock(),
        vector_store=AsyncMock(),
    )
    # Mock search on hybrid
    candidates = [
        _create_sample_hybrid_result(point_id="p1", score=0.02, content="Text 1"),
        _create_sample_hybrid_result(point_id="p2", score=0.01, content="Text 2"),
    ]
    hybrid.search = AsyncMock(return_value=candidates)  # type: ignore[method-assign]

    mock_reranker = MagicMock(spec=CrossEncoderReranker)
    expected = [
        RerankedSearchResult(
            point_id="p1",
            document_id="doc-1",
            chunk_index=0,
            content="Text 1",
            token_count=2,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=0.99,
            rrf_score=0.02,
            score=0.99,
            rank=1,
        )
    ]
    mock_reranker.arerank = AsyncMock(return_value=expected)

    results = await hybrid.search_and_rerank(
        query="query",
        top_k=1,
        candidate_k=5,
        reranker=mock_reranker,
    )

    assert results == expected
    mock_hybrid_search = hybrid.search
    mock_hybrid_search.assert_awaited_once_with(
        query="query",
        top_k=5,
        candidate_k=5,
        document_id=None,
    )
    mock_reranker.arerank.assert_awaited_once_with(
        query="query",
        candidates=candidates,
        top_k=1,
    )
