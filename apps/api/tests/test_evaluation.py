import tempfile
from pathlib import Path

import pytest

from src.evaluation import (
    EvalDataset,
    EvalItem,
    RAGEvaluator,
    calculate_citation_validity,
    calculate_keyword_coverage,
    calculate_mrr,
    calculate_ndcg_at_k,
    calculate_no_evidence_refusal,
    calculate_precision_at_k,
    calculate_recall_at_k,
)


def test_recall_at_k():
    retrieved = ["doc-1", "doc-2", "doc-3"]
    relevant = ["doc-2", "doc-4"]

    assert calculate_recall_at_k(retrieved, relevant, k=1) == 0.0
    assert calculate_recall_at_k(retrieved, relevant, k=2) == 0.5
    assert calculate_recall_at_k(retrieved, relevant, k=3) == 0.5
    assert calculate_recall_at_k(retrieved, [], k=5) == 1.0


def test_precision_at_k():
    retrieved = ["doc-1", "doc-2", "doc-3"]
    relevant = ["doc-2", "doc-4"]

    assert calculate_precision_at_k(retrieved, relevant, k=1) == 0.0
    assert calculate_precision_at_k(retrieved, relevant, k=2) == 0.5
    assert pytest.approx(calculate_precision_at_k(retrieved, relevant, k=3), 0.01) == 0.333
    assert calculate_precision_at_k([], relevant, k=5) == 0.0


def test_mrr():
    assert calculate_mrr(["doc-1", "doc-2", "doc-3"], ["doc-1"]) == 1.0
    assert calculate_mrr(["doc-1", "doc-2", "doc-3"], ["doc-2"]) == 0.5
    assert calculate_mrr(["doc-1", "doc-2", "doc-3"], ["doc-3"]) == 1.0 / 3.0
    assert calculate_mrr(["doc-1", "doc-2"], ["doc-99"]) == 0.0
    assert calculate_mrr([], ["doc-1"]) == 0.0


def test_ndcg_at_k():
    perfect = ["doc-1", "doc-2", "doc-3"]
    assert calculate_ndcg_at_k(perfect, ["doc-1", "doc-2"], k=2) == 1.0

    inverted = ["doc-3", "doc-1", "doc-2"]
    ndcg_inverted = calculate_ndcg_at_k(inverted, ["doc-1", "doc-2"], k=3)
    assert 0.0 < ndcg_inverted < 1.0


def test_citation_validity():
    # Valid citations
    assert calculate_citation_validity("Redis uses LRU [1] and LFU [2].", max_source_id=2) == 1.0
    assert calculate_citation_validity("No citations here.", max_source_id=2) == 1.0

    # Hallucinated citation beyond provided sources
    assert calculate_citation_validity("Invalid citation [3] used.", max_source_id=2) == 0.0
    assert calculate_citation_validity("Citation [0] is invalid.", max_source_id=2) == 0.0

    # Citation present when 0 sources were provided
    assert calculate_citation_validity("Some fact [1].", max_source_id=0) == 0.0


def test_no_evidence_refusal():
    refusal_answer = "The provided documents do not contain enough information to answer this question."
    hallucinated_answer = "Redis was invented in 1845 by Thomas Edison."

    assert calculate_no_evidence_refusal(refusal_answer, should_refuse=True) == 1.0
    assert calculate_no_evidence_refusal(hallucinated_answer, should_refuse=True) == 0.0
    assert calculate_no_evidence_refusal(hallucinated_answer, should_refuse=False) == 1.0
    assert calculate_no_evidence_refusal(refusal_answer, should_refuse=False) == 0.0


def test_keyword_coverage():
    answer = "Redis supports LRU key eviction to manage limited memory."
    assert calculate_keyword_coverage(answer, ["eviction", "memory", "redis"]) == 1.0
    assert calculate_keyword_coverage(answer, ["eviction", "postgres"]) == 0.5
    assert calculate_keyword_coverage(answer, []) == 1.0


def test_dataset_json_and_jsonl():
    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = Path(tmpdir) / "test.json"
        jsonl_file = Path(tmpdir) / "test.jsonl"

        dataset = EvalDataset(
            items=[
                EvalItem(
                    question="Q1",
                    expected_answer="A1",
                    expected_document_ids=["d1"],
                    expected_keywords=["k1"],
                ),
                EvalItem(
                    question="Q2",
                    expected_answer="A2",
                    expected_document_ids=["d2"],
                    should_refuse=True,
                ),
            ]
        )

        dataset.save_json(json_file)
        loaded_json = EvalDataset.from_json(json_file)
        assert len(loaded_json.items) == 2
        assert loaded_json.items[0].question == "Q1"

        dataset.save_jsonl(jsonl_file)
        loaded_jsonl = EvalDataset.from_jsonl(jsonl_file)
        assert len(loaded_jsonl.items) == 2
        assert loaded_jsonl.items[1].should_refuse is True


@pytest.mark.asyncio
async def test_ragevaluator_pipeline():
    dataset = EvalDataset(
        items=[
            EvalItem(
                question="What is Redis eviction?",
                expected_document_ids=["doc-redis"],
                expected_keywords=["eviction"],
                should_refuse=False,
            ),
            EvalItem(
                question="Unknown topic?",
                expected_document_ids=[],
                expected_keywords=[],
                should_refuse=True,
            ),
        ]
    )

    async def mock_query(question: str):
        if "Redis" in question:
            return (["doc-redis", "doc-other"], "Redis uses eviction [1].", 2)
        else:
            return ([], "The provided documents do not contain enough information.", 0)

    evaluator = RAGEvaluator(dataset)
    summary = await evaluator.evaluate_pipeline(mock_query)

    assert summary.total_cases == 2
    assert summary.avg_recall_at_5 == 1.0
    assert summary.avg_mrr == 1.0
    assert summary.citation_validity_rate == 1.0
    assert summary.no_evidence_accuracy_rate == 1.0

    text_report = summary.format_text_report()
    assert "SEARCH SPHERE RAG EVALUATION" in text_report
    assert "Recall@5:               1.0000" in text_report
