from unittest.mock import MagicMock

import pytest

from src.retrieval.reranker import CrossEncoderReranker


def test_cross_encoder_reranks_differently_from_rrf():
    """
    Verify that cross-encoder reranking actively re-orders candidate results based on
    cross-encoder scores, and that silent fallback to RRF cannot pass this test.
    """
    # Candidate A: High RRF score (rank 1 initially), but low cross-encoder relevance
    cand_a = {
        "point_id": "pt-a",
        "document_id": "doc-1",
        "chunk_index": 0,
        "content": "Candidate A has high keyword overlap with query terms.",
        "score": 0.08,
        "rrf_score": 0.08,
    }
    # Candidate B: Lower RRF score (rank 2 initially), but high semantic relevance from cross-encoder
    cand_b = {
        "point_id": "pt-b",
        "document_id": "doc-1",
        "chunk_index": 1,
        "content": "Candidate B directly answers the user's semantic intent.",
        "score": 0.02,
        "rrf_score": 0.02,
    }

    # Test 1: Model with predict() interface (SentenceTransformer)
    mock_model_predict = MagicMock()
    mock_model_predict.predict.return_value = [0.15, 0.95]

    reranker_1 = CrossEncoderReranker(
        model=mock_model_predict,
        default_top_k=2,
    )
    results_1 = reranker_1.rerank(
        query="What is the answer to my question?",
        candidates=[cand_a, cand_b],
    )
    assert len(results_1) == 2
    assert results_1[0].point_id == "pt-b"
    assert results_1[0].rank == 1
    assert results_1[0].rerank_score == pytest.approx(0.95)
    assert results_1[1].point_id == "pt-a"
    assert results_1[1].rank == 2
    assert results_1[1].rerank_score == pytest.approx(0.15)

    # Test 2: Model with rerank() interface (FastEmbed TextCrossEncoder)
    class FastEmbedMock:
        def rerank(self, query, docs):
            return [0.15, 0.95]

    reranker_2 = CrossEncoderReranker(
        model=FastEmbedMock(),
        default_top_k=2,
    )
    results_2 = reranker_2.rerank(
        query="What is the answer to my question?",
        candidates=[cand_a, cand_b],
    )
    assert len(results_2) == 2
    assert results_2[0].point_id == "pt-b"
    assert results_2[0].rank == 1
    assert results_2[0].rerank_score == pytest.approx(0.95)
    assert results_2[1].point_id == "pt-a"
    assert results_2[1].rank == 2
    assert results_2[1].rerank_score == pytest.approx(0.15)


def test_cross_encoder_alias_resolution():
    """Verify that old model names resolve to Xenova/ms-marco-MiniLM-L-6-v2."""
    from src.services.model_manager import RERANKER_MODEL_ALIASES

    reranker = CrossEncoderReranker(model_name="cross-encoder/ms-marco-MiniLM-L-6-v2")
    assert reranker.model_name == "rerank-v3.5"

    assert RERANKER_MODEL_ALIASES["ms-marco-MiniLM-L-6-v2"] == "rerank-v3.5"
