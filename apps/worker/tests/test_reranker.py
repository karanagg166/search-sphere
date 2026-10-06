import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.processing.models.search import HybridSearchResult, RerankedSearchResult
from src.retrieval.reranker import (
    CrossEncoderReranker,
    RerankerError,
    RerankerValidationError,
    RerankingError,
    RerankingValidationError,
)


def _create_sample_hybrid_result(
    point_id: str = "point-1",
    score: float = 0.033,
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Sample chunk content for testing",
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
# 1. Query Validation
# ==============================================================================


def test_empty_query_raises_validation_error() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingValidationError, match="non-empty"):
        reranker.rerank(query="", candidates=candidates)

    mock_model.predict.assert_not_called()


def test_whitespace_only_query_raises_validation_error() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingValidationError, match="non-empty"):
        reranker.rerank(query="   \t\n  ", candidates=candidates)

    mock_model.predict.assert_not_called()


@pytest.mark.parametrize("invalid_query", [None, 123, ["query"], {"q": "text"}])
def test_non_string_query_raises_validation_error(invalid_query: object) -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingValidationError, match="non-empty"):
        reranker.rerank(query=invalid_query, candidates=candidates)  # type: ignore[arg-type]


def test_query_whitespace_trimmed_before_scoring() -> None:
    mock_model = MagicMock()
    mock_model.predict.return_value = [0.9]
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result(content="Document text")]

    reranker.rerank(query="  how does caching work?  ", candidates=candidates)

    mock_model.predict.assert_called_once_with(
        [("how does caching work?", "Document text")],
        batch_size=32,
        show_progress_bar=False,
    )


# ==============================================================================
# 2. Candidate Validation & Handling
# ==============================================================================


def test_empty_candidates_returns_empty_list_cleanly() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)

    results = reranker.rerank(query="valid query", candidates=[])

    assert results == []
    assert isinstance(results, list)
    mock_model.predict.assert_not_called()


@pytest.mark.parametrize(
    "invalid_candidates", [None, 123, "candidates", {"doc": "content"}]
)
def test_invalid_candidates_type_raises_validation_error(
    invalid_candidates: object,
) -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)

    with pytest.raises(RerankingValidationError, match="list or sequence"):
        reranker.rerank(query="valid query", candidates=invalid_candidates)  # type: ignore[arg-type]


def test_candidate_without_content_raises_validation_error() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)

    invalid_item = MagicMock(spec=[])  # has no .content attribute

    with pytest.raises(RerankingValidationError, match="valid text content"):
        reranker.rerank(query="valid query", candidates=[invalid_item])


def test_candidate_with_non_string_content_raises_validation_error() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)

    invalid_item = MagicMock()
    invalid_item.content = 12345

    with pytest.raises(RerankingValidationError, match="valid text content"):
        reranker.rerank(query="valid query", candidates=[invalid_item])


# ==============================================================================
# 3. Top-K Validation & Configuration
# ==============================================================================


@pytest.mark.parametrize("invalid_k", [0, -1, -10, "5", 3.14, True, False])
def test_invalid_top_k_raises_validation_error(invalid_k: object) -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingValidationError, match="positive integer"):
        reranker.rerank(query="valid query", candidates=candidates, top_k=invalid_k)  # type: ignore[arg-type]


def test_top_k_exceeding_max_raises_validation_error() -> None:
    mock_model = MagicMock()
    reranker = CrossEncoderReranker(model=mock_model, max_top_k=50)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingValidationError, match="exceeds maximum"):
        reranker.rerank(query="valid query", candidates=candidates, top_k=51)


def test_default_top_k_applied_when_none() -> None:
    mock_model = MagicMock()
    # 5 candidates with corresponding scores
    candidates = [_create_sample_hybrid_result(point_id=f"p{i}") for i in range(5)]
    mock_model.predict.return_value = [0.1, 0.5, 0.2, 0.9, 0.4]

    reranker = CrossEncoderReranker(model=mock_model, default_top_k=3)
    results = reranker.rerank(query="valid query", candidates=candidates, top_k=None)

    assert len(results) == 3


def test_top_k_exceeding_candidate_count_returns_all_candidates() -> None:
    """
    If top_k > candidate count, return all available candidates rather than
    failing.
    """
    mock_model = MagicMock()
    candidates = [
        _create_sample_hybrid_result(point_id="p1", content="Chunk 1"),
        _create_sample_hybrid_result(point_id="p2", content="Chunk 2"),
    ]
    mock_model.predict.return_value = [0.8, 0.3]

    reranker = CrossEncoderReranker(model=mock_model, default_top_k=5)
    results = reranker.rerank(query="valid query", candidates=candidates, top_k=10)

    assert len(results) == 2
    assert results[0].point_id == "p1"
    assert results[1].point_id == "p2"


# ==============================================================================
# 4. Scoring, Sorting & Truncation
# ==============================================================================


def test_scoring_multiple_candidates_and_sorting_descending() -> None:
    mock_model = MagicMock()
    candidates = [
        _create_sample_hybrid_result(point_id="p-low", content="Low relevance text"),
        _create_sample_hybrid_result(point_id="p-high", content="High relevance text"),
        _create_sample_hybrid_result(point_id="p-mid", content="Medium relevance text"),
    ]
    # Predict returns scores in same order as candidates
    mock_model.predict.return_value = [0.12, 0.95, 0.64]

    reranker = CrossEncoderReranker(model=mock_model)
    results = reranker.rerank(query="test query", candidates=candidates, top_k=3)

    assert len(results) == 3
    # Order should be p-high (0.95), p-mid (0.64), p-low (0.12)
    assert results[0].point_id == "p-high"
    assert results[0].rerank_score == 0.95
    assert results[0].score == 0.95
    assert results[0].rank == 1

    assert results[1].point_id == "p-mid"
    assert results[1].rerank_score == 0.64
    assert results[1].score == 0.64
    assert results[1].rank == 2

    assert results[2].point_id == "p-low"
    assert results[2].rerank_score == 0.12
    assert results[2].score == 0.12
    assert results[2].rank == 3


def test_top_k_truncation() -> None:
    mock_model = MagicMock()
    candidates = [
        _create_sample_hybrid_result(point_id="p1", content="One"),
        _create_sample_hybrid_result(point_id="p2", content="Two"),
        _create_sample_hybrid_result(point_id="p3", content="Three"),
        _create_sample_hybrid_result(point_id="p4", content="Four"),
    ]
    mock_model.predict.return_value = [0.4, 0.9, 0.1, 0.7]

    reranker = CrossEncoderReranker(model=mock_model)
    results = reranker.rerank(query="test query", candidates=candidates, top_k=2)

    assert len(results) == 2
    assert results[0].point_id == "p2"
    assert results[0].rerank_score == 0.9
    assert results[0].rank == 1

    assert results[1].point_id == "p4"
    assert results[1].rerank_score == 0.7
    assert results[1].rank == 2


# ==============================================================================
# 5. Metadata and Score Preservation
# ==============================================================================


def test_metadata_and_rrf_score_preservation() -> None:
    mock_model = MagicMock()
    candidate = HybridSearchResult(
        point_id="point-uuid-123",
        score=0.01639,  # Qdrant RRF fusion score
        document_id="doc-uuid-999",
        chunk_index=4,
        content="Architecture of the distributed cluster",
        token_count=42,
        start_page=3,
        end_page=5,
        page_numbers=[3, 4, 5],
        block_types=["header", "text"],
        rank=2,
    )
    mock_model.predict.return_value = [0.884]

    reranker = CrossEncoderReranker(model=mock_model)
    results = reranker.rerank(
        query="cluster architecture",
        candidates=[candidate],
        top_k=1,
    )

    assert len(results) == 1
    res = results[0]
    assert isinstance(res, RerankedSearchResult)
    assert res.point_id == "point-uuid-123"
    assert res.document_id == "doc-uuid-999"
    assert res.chunk_index == 4
    assert res.content == "Architecture of the distributed cluster"
    assert res.token_count == 42
    assert res.start_page == 3
    assert res.end_page == 5
    assert res.page_numbers == [3, 4, 5]
    assert res.block_types == ["header", "text"]

    # RRF score preserved separately, NOT overwritten by rerank_score
    assert res.rrf_score == 0.01639
    assert res.rerank_score == 0.884
    assert res.score == 0.884
    assert res.rank == 1


def test_dictionary_candidate_compatibility() -> None:
    mock_model = MagicMock()
    dict_cand = {
        "point_id": "dict-p1",
        "document_id": "dict-doc",
        "chunk_index": 2,
        "content": "Text content from dictionary candidate",
        "token_count": 6,
        "start_page": 2,
        "end_page": 2,
        "page_numbers": [2],
        "block_types": ["text"],
        "score": 0.025,
    }
    mock_model.predict.return_value = [0.75]

    reranker = CrossEncoderReranker(model=mock_model)
    results = reranker.rerank(query="query", candidates=[dict_cand], top_k=1)

    assert len(results) == 1
    assert results[0].point_id == "dict-p1"
    assert results[0].document_id == "dict-doc"
    assert results[0].chunk_index == 2
    assert results[0].content == "Text content from dictionary candidate"
    assert results[0].rrf_score == 0.025
    assert results[0].rerank_score == 0.75
    assert results[0].rank == 1


# ==============================================================================
# 6. Error Handling & Score Validation
# ==============================================================================


def test_model_predict_exception_wrapped_in_reranking_error() -> None:
    mock_model = MagicMock()
    mock_model.predict.side_effect = RuntimeError("CUDA out of memory")
    reranker = CrossEncoderReranker(model=mock_model)
    candidates = [_create_sample_hybrid_result()]

    with pytest.raises(RerankingError, match="Failed to score candidates"):
        reranker._score_pairs([("query", candidate.content) for candidate in candidates])


def test_score_count_mismatch_raises_reranking_error() -> None:
    mock_model = MagicMock()
    # 2 candidates, but model returns 1 score
    candidates = [
        _create_sample_hybrid_result(point_id="p1"),
        _create_sample_hybrid_result(point_id="p2"),
    ]
    mock_model.predict.return_value = [0.8]

    reranker = CrossEncoderReranker(model=mock_model)

    with pytest.raises(RerankingError, match="Score count mismatch"):
        reranker._score_pairs([("query", candidate.content) for candidate in candidates])


def test_nan_score_raises_reranking_error() -> None:
    mock_model = MagicMock()
    candidates = [_create_sample_hybrid_result()]
    mock_model.predict.return_value = [float("nan")]

    reranker = CrossEncoderReranker(model=mock_model)

    with pytest.raises(RerankingError, match="NaN detected"):
        reranker._score_pairs([("query", candidate.content) for candidate in candidates])


def test_infinity_score_raises_reranking_error() -> None:
    mock_model = MagicMock()
    candidates = [_create_sample_hybrid_result()]
    mock_model.predict.return_value = [float("inf")]

    reranker = CrossEncoderReranker(model=mock_model)

    with pytest.raises(RerankingError, match="Infinity detected"):
        reranker._score_pairs([("query", candidate.content) for candidate in candidates])


def test_non_numeric_score_raises_reranking_error() -> None:
    mock_model = MagicMock()
    candidates = [_create_sample_hybrid_result()]
    mock_model.predict.return_value = ["not-a-number"]

    reranker = CrossEncoderReranker(model=mock_model)

    with pytest.raises(RerankingError, match="Non-numeric rerank score"):
        reranker._score_pairs([("query", candidate.content) for candidate in candidates])


def test_callable_model_fallback() -> None:
    """
    Ensure callable model (e.g. mock function or PyTorch Module) works
    without .predict.
    """
    candidates = [
        _create_sample_hybrid_result(point_id="p1", content="One"),
        _create_sample_hybrid_result(point_id="p2", content="Two"),
    ]

    def mock_callable(pairs: list[tuple[str, str]]) -> list[float]:
        return [0.3, 0.85]

    reranker = CrossEncoderReranker(model=mock_callable)
    results = reranker.rerank(query="test", candidates=candidates, top_k=2)

    assert len(results) == 2
    assert results[0].point_id == "p2"
    assert results[0].rerank_score == 0.85
    assert results[1].point_id == "p1"
    assert results[1].rerank_score == 0.3


# ==============================================================================
# 7. Async Support
# ==============================================================================


@pytest.mark.asyncio
async def test_arerank_runs_asynchronously() -> None:
    mock_model = MagicMock()
    candidates = [
        _create_sample_hybrid_result(point_id="p1", content="Async chunk 1"),
        _create_sample_hybrid_result(point_id="p2", content="Async chunk 2"),
    ]
    mock_model.predict.return_value = [0.45, 0.92]

    reranker = CrossEncoderReranker(model=mock_model)
    results = await reranker.arerank(
        query="async query",
        candidates=candidates,
        top_k=2,
    )

    assert len(results) == 2
    assert results[0].point_id == "p2"
    assert results[0].rerank_score == 0.92
    assert results[1].point_id == "p1"
    assert results[1].rerank_score == 0.45


# ==============================================================================
# 8. Lazy Loading, Caching & Settings Configuration
# ==============================================================================


def test_lazy_loading_and_caching() -> None:
    CrossEncoderReranker._clear_cache()

    with patch("sentence_transformers.CrossEncoder") as mock_ce_class:
        mock_instance = MagicMock()
        mock_instance.predict.return_value = [0.8]
        mock_ce_class.return_value = mock_instance

        reranker1 = CrossEncoderReranker(model_name="custom/model-name", device="cpu")
        candidates = [_create_sample_hybrid_result()]

        res1 = reranker1.rerank("query", candidates)
        assert len(res1) == 1
        mock_ce_class.assert_called_once_with("custom/model-name", device="cpu")

        # Second instance with same model name reuses cached model
        reranker2 = CrossEncoderReranker(model_name="custom/model-name", device="cpu")
        res2 = reranker2.rerank("query", candidates)
        assert len(res2) == 1
        # Class constructor still only called once
        mock_ce_class.assert_called_once()

    CrossEncoderReranker._clear_cache()


def test_model_loading_failure_raises_reranking_error() -> None:
    CrossEncoderReranker._clear_cache()

    with patch(
        "sentence_transformers.CrossEncoder",
        side_effect=Exception("Model not found"),
    ):
        reranker = CrossEncoderReranker(model_name="nonexistent/model")

        with pytest.raises(RerankingError, match="Failed to load cross-encoder model"):
            reranker._get_model()

    CrossEncoderReranker._clear_cache()


def test_custom_parameters_forwarded() -> None:
    mock_model = MagicMock()
    mock_model.predict.return_value = [0.5]

    reranker = CrossEncoderReranker(
        model=mock_model,
        batch_size=16,
        default_top_k=2,
    )
    candidates = [_create_sample_hybrid_result()]
    reranker.rerank(query="query", candidates=candidates)

    mock_model.predict.assert_called_once_with(
        [("query", "Sample chunk content for testing")],
        batch_size=16,
        show_progress_bar=False,
    )


def test_exception_aliases() -> None:
    assert issubclass(RerankerError, Exception)
    assert issubclass(RerankingError, Exception)
    assert issubclass(RerankingValidationError, RerankingError)
    assert issubclass(RerankerValidationError, RerankerError)


@pytest.fixture(autouse=True)
def isolate_loader_dependencies(monkeypatch):
    # Loader tests patch this constructor; all scoring tests inject their model.
    # Importing PyTorch here would not test any additional scoring behavior.
    CrossEncoderReranker._clear_cache()
    monkeypatch.setattr("fastembed.rerank.cross_encoder.TextCrossEncoder", MagicMock(side_effect=ImportError("unit test fallback")))
    with patch.dict(sys.modules, {"sentence_transformers": SimpleNamespace(CrossEncoder=MagicMock())}):
        yield
    CrossEncoderReranker._clear_cache()


@pytest.mark.parametrize("scores", [[float("nan")], [float("inf")], ["invalid"], []])
def test_rerank_invalid_model_scores_preserve_rrf_order(scores):
    candidates = [
        _create_sample_hybrid_result(point_id="low", score=0.1),
        _create_sample_hybrid_result(point_id="high", score=0.9),
    ]
    model = MagicMock(spec=["predict"])
    model.predict.return_value = scores
    results = CrossEncoderReranker(model=model).rerank("query", candidates, top_k=1)
    assert len(results) == 1
    assert results[0].point_id == "high"
    assert results[0].rerank_score == 0.9
    assert results[0].rrf_score == 0.9


def test_rerank_inference_failure_preserves_rrf_order():
    candidates = [_create_sample_hybrid_result(point_id="p1", score=0.7)]
    model = MagicMock(spec=["predict"])
    model.predict.side_effect = RuntimeError("inference failed")
    result = CrossEncoderReranker(model=model).rerank("query", candidates)
    assert result[0].point_id == "p1"
    assert result[0].rerank_score == 0.7
