import pytest
from pydantic import ValidationError

from src.evaluation import (
    EvalDataset,
    EvalItem,
    EvaluationSummary,
    SingleEvalResult,
)
from src.evaluation.tuning import ComparisonResult, ExperimentRunner, TuningConfig


def test_tuning_config_validation():
    valid_cfg = TuningConfig(
        name="Experimental-1",
        candidate_k=40,
        top_k=8,
        chunk_size=400,
        chunk_overlap=50,
        rag_temperature=0.2,
    )
    assert valid_cfg.name == "Experimental-1"
    assert valid_cfg.candidate_k == 40
    assert valid_cfg.top_k == 8

    # candidate_k must be > 0
    with pytest.raises(ValidationError):
        TuningConfig(name="Bad", candidate_k=0)

    # top_k must be > 0
    with pytest.raises(ValidationError):
        TuningConfig(name="Bad", top_k=-1)

    # temperature must be between 0.0 and 2.0
    with pytest.raises(ValidationError):
        TuningConfig(name="Bad", rag_temperature=2.5)


def test_comparison_result_deltas_and_table():
    cfg_a = TuningConfig(name="Baseline", candidate_k=20, top_k=5)
    cfg_b = TuningConfig(name="CandidateK-40", candidate_k=40, top_k=5)

    sum_a = EvaluationSummary(
        total_cases=10,
        avg_recall_at_5=0.70,
        avg_precision_at_5=0.50,
        avg_mrr=0.65,
        avg_ndcg_at_5=0.68,
        citation_validity_rate=0.90,
        no_evidence_accuracy_rate=0.95,
        avg_keyword_coverage=0.80,
    )
    sum_b = EvaluationSummary(
        total_cases=10,
        avg_recall_at_5=0.85,
        avg_precision_at_5=0.52,
        avg_mrr=0.75,
        avg_ndcg_at_5=0.78,
        citation_validity_rate=0.95,
        no_evidence_accuracy_rate=0.95,
        avg_keyword_coverage=0.85,
    )

    runner = ExperimentRunner(dataset=EvalDataset(items=[]))
    comp = runner.compare_summaries(cfg_a, cfg_b, sum_a, sum_b)

    assert pytest.approx(comp.delta_recall_at_5, 0.001) == 0.15
    assert pytest.approx(comp.delta_precision_at_5, 0.001) == 0.02
    assert pytest.approx(comp.delta_mrr, 0.001) == 0.10
    assert pytest.approx(comp.delta_citation_validity, 0.001) == 0.05
    assert comp.delta_no_evidence_accuracy == 0.0

    table = comp.format_comparison_table()
    assert "TUNING EXPERIMENT: Baseline VS CandidateK-40" in table
    assert "+0.1500" in table
    assert "+20" in table  # delta candidate_k


@pytest.mark.asyncio
async def test_experiment_runner_run_comparison():
    dataset = EvalDataset(
        items=[
            EvalItem(
                question="What is Qdrant?",
                expected_document_ids=["doc-qdrant"],
                expected_keywords=["vector", "database"],
                should_refuse=False,
            ),
        ]
    )

    cfg_a = TuningConfig(name="SmallK", candidate_k=10, top_k=2)
    cfg_b = TuningConfig(name="LargeK", candidate_k=30, top_k=5)

    def mock_pipeline_factory(config: TuningConfig):
        async def query_fn(question: str):
            if config.candidate_k > 20:
                # LargeK finds the target doc
                return (["doc-qdrant"], "Qdrant is a vector database [1].", 1)
            else:
                # SmallK misses it
                return (["doc-other"], "Unrelated content.", 1)

        return query_fn

    runner = ExperimentRunner(dataset=dataset)
    comp = await runner.run_comparison(cfg_a, cfg_b, mock_pipeline_factory)

    assert comp.summary_a.avg_recall_at_5 == 0.0
    assert comp.summary_b.avg_recall_at_5 == 1.0
    assert comp.delta_recall_at_5 == 1.0
