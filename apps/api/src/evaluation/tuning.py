from typing import Any, Callable, Coroutine
from pydantic import BaseModel, Field

from src.evaluation.dataset import EvalDataset
from src.evaluation.evaluator import EvaluationSummary, RAGEvaluator


class TuningConfig(BaseModel):
    """Configuration parameters for retrieval & RAG tuning experiments."""

    name: str = Field(..., description="Human-readable identifier for this experiment configuration.")
    candidate_k: int = Field(default=20, gt=0, description="Pre-reranking hybrid retrieval candidate count.")
    top_k: int = Field(default=5, gt=0, description="Final reranked top-K chunks passed to generation.")
    chunk_size: int = Field(default=500, gt=0, description="Chunk target token size.")
    chunk_overlap: int = Field(default=60, ge=0, description="Chunk token overlap.")
    semantic_distance_threshold: float = Field(
        default=0.5, ge=0.0, le=1.0, description="Cosine distance threshold for semantic splitting."
    )
    dense_model: str = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Dense embedding model identifier.",
    )
    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="Cross-encoder reranking model identifier.",
    )
    score_threshold: float | None = Field(
        default=None, description="Optional minimum score cutoff for retrieved chunks."
    )
    rag_temperature: float = Field(
        default=0.1, ge=0.0, le=2.0, description="Generation sampling temperature."
    )


class ComparisonResult(BaseModel):
    """Side-by-side comparison of two experiment configurations."""

    config_a: TuningConfig
    config_b: TuningConfig
    summary_a: EvaluationSummary
    summary_b: EvaluationSummary
    delta_recall_at_5: float
    delta_precision_at_5: float
    delta_mrr: float
    delta_ndcg_at_5: float
    delta_citation_validity: float
    delta_no_evidence_accuracy: float

    def format_comparison_table(self) -> str:
        """Format Markdown comparison report."""
        return (
            "========================================================================\n"
            f"          TUNING EXPERIMENT: {self.config_a.name} VS {self.config_b.name}\n"
            "========================================================================\n"
            f"Parameter                   | Config A ({self.config_a.name[:12]}) | Config B ({self.config_b.name[:12]}) | Delta\n"
            f"candidate_k                 | {self.config_a.candidate_k:<20} | {self.config_b.candidate_k:<20} | {self.config_b.candidate_k - self.config_a.candidate_k:+d}\n"
            f"top_k                       | {self.config_a.top_k:<20} | {self.config_b.top_k:<20} | {self.config_b.top_k - self.config_a.top_k:+d}\n"
            f"chunk_size                  | {self.config_a.chunk_size:<20} | {self.config_b.chunk_size:<20} | {self.config_b.chunk_size - self.config_a.chunk_size:+d}\n"
            f"------------------------------------------------------------------------\n"
            f"Metric                      | Config A             | Config B             | Delta\n"
            f"Recall@5                    | {self.summary_a.avg_recall_at_5:.4f}               | {self.summary_b.avg_recall_at_5:.4f}               | {self.delta_recall_at_5:+.4f}\n"
            f"Precision@5                 | {self.summary_a.avg_precision_at_5:.4f}               | {self.summary_b.avg_precision_at_5:.4f}               | {self.delta_precision_at_5:+.4f}\n"
            f"MRR                         | {self.summary_a.avg_mrr:.4f}               | {self.summary_b.avg_mrr:.4f}               | {self.delta_mrr:+.4f}\n"
            f"nDCG@5                      | {self.summary_a.avg_ndcg_at_5:.4f}               | {self.summary_b.avg_ndcg_at_5:.4f}               | {self.delta_ndcg_at_5:+.4f}\n"
            f"Citation Validity           | {self.summary_a.citation_validity_rate * 100:.1f}%                | {self.summary_b.citation_validity_rate * 100:.1f}%                | {self.delta_citation_validity * 100:+.1f}%\n"
            f"No-Evidence Accuracy        | {self.summary_a.no_evidence_accuracy_rate * 100:.1f}%                | {self.summary_b.no_evidence_accuracy_rate * 100:.1f}%                | {self.delta_no_evidence_accuracy * 100:+.1f}%\n"
            "========================================================================\n"
        )


class ExperimentRunner:
    """
    Executes controlled tuning experiments across multiple configurations.
    """

    def __init__(self, dataset: EvalDataset) -> None:
        self.dataset = dataset
        self.evaluator = RAGEvaluator(dataset)

    def compare_summaries(
        self,
        config_a: TuningConfig,
        config_b: TuningConfig,
        summary_a: EvaluationSummary,
        summary_b: EvaluationSummary,
    ) -> ComparisonResult:
        """Compute delta metrics between two evaluation summaries."""
        return ComparisonResult(
            config_a=config_a,
            config_b=config_b,
            summary_a=summary_a,
            summary_b=summary_b,
            delta_recall_at_5=summary_b.avg_recall_at_5 - summary_a.avg_recall_at_5,
            delta_precision_at_5=summary_b.avg_precision_at_5 - summary_a.avg_precision_at_5,
            delta_mrr=summary_b.avg_mrr - summary_a.avg_mrr,
            delta_ndcg_at_5=summary_b.avg_ndcg_at_5 - summary_a.avg_ndcg_at_5,
            delta_citation_validity=summary_b.citation_validity_rate - summary_a.citation_validity_rate,
            delta_no_evidence_accuracy=summary_b.no_evidence_accuracy_rate - summary_a.no_evidence_accuracy_rate,
        )

    async def run_experiment(
        self,
        config: TuningConfig,
        pipeline_factory: Callable[[TuningConfig], Callable[[str], Coroutine[Any, Any, tuple[list[str], str, int]]]],
    ) -> EvaluationSummary:
        """Run evaluation for a single configuration."""
        query_fn = pipeline_factory(config)
        return await self.evaluator.evaluate_pipeline(query_fn)

    async def run_comparison(
        self,
        config_a: TuningConfig,
        config_b: TuningConfig,
        pipeline_factory: Callable[[TuningConfig], Callable[[str], Coroutine[Any, Any, tuple[list[str], str, int]]]],
    ) -> ComparisonResult:
        """Run evaluation on Config A and Config B and return ComparisonResult."""
        summary_a = await self.run_experiment(config_a, pipeline_factory)
        summary_b = await self.run_experiment(config_b, pipeline_factory)
        return self.compare_summaries(config_a, config_b, summary_a, summary_b)
