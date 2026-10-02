from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from src.evaluation.dataset import EvalDataset, EvalItem
from src.evaluation.metrics import (
    calculate_citation_validity,
    calculate_keyword_coverage,
    calculate_mrr,
    calculate_ndcg_at_k,
    calculate_no_evidence_refusal,
    calculate_precision_at_k,
    calculate_recall_at_k,
)


class SingleEvalResult(BaseModel):
    """Result of evaluating a single test case."""

    question: str
    expected_doc_ids: list[str]
    retrieved_doc_ids: list[str]
    generated_answer: str
    sources_count: int
    recall_at_5: float
    precision_at_5: float
    mrr: float
    ndcg_at_5: float
    citation_validity: float
    no_evidence_accuracy: float
    keyword_coverage: float


class EvaluationSummary(BaseModel):
    """Aggregated evaluation metrics across the test dataset."""

    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    total_cases: int
    avg_recall_at_5: float
    avg_precision_at_5: float
    avg_mrr: float
    avg_ndcg_at_5: float
    citation_validity_rate: float
    no_evidence_accuracy_rate: float
    avg_keyword_coverage: float
    results: list[SingleEvalResult] = []

    def format_text_report(self) -> str:
        """Generate human-readable evaluation summary table."""
        return (
            "========================================================\n"
            "                SEARCH SPHERE RAG EVALUATION            \n"
            "========================================================\n"
            f"Timestamp:                {self.timestamp}\n"
            f"Total Test Cases:         {self.total_cases}\n"
            "--------------------------------------------------------\n"
            "RETRIEVAL METRICS:\n"
            f"  Recall@5:               {self.avg_recall_at_5:.4f}\n"
            f"  Precision@5:            {self.avg_precision_at_5:.4f}\n"
            f"  Mean Reciprocal Rank:   {self.avg_mrr:.4f}\n"
            f"  nDCG@5:                 {self.avg_ndcg_at_5:.4f}\n"
            "--------------------------------------------------------\n"
            "GENERATION & CITATION METRICS:\n"
            f"  Citation Validity Rate: {self.citation_validity_rate * 100:.1f}%\n"
            f"  No-Evidence Accuracy:   {self.no_evidence_accuracy_rate * 100:.1f}%\n"
            f"  Keyword Coverage:       {self.avg_keyword_coverage * 100:.1f}%\n"
            "========================================================\n"
        )


class RAGEvaluator:
    """
    Evaluator executing test suites against retrieval & answer generation pipelines.
    """

    def __init__(self, dataset: EvalDataset) -> None:
        self.dataset = dataset

    def evaluate_item(
        self,
        item: EvalItem,
        retrieved_doc_ids: list[str],
        generated_answer: str,
        sources_count: int,
    ) -> SingleEvalResult:
        """Compute metrics for a single question evaluation."""
        rec5 = calculate_recall_at_k(retrieved_doc_ids, item.expected_document_ids, k=5)
        prec5 = calculate_precision_at_k(retrieved_doc_ids, item.expected_document_ids, k=5)
        mrr = calculate_mrr(retrieved_doc_ids, item.expected_document_ids)
        ndcg5 = calculate_ndcg_at_k(retrieved_doc_ids, item.expected_document_ids, k=5)

        cit_val = calculate_citation_validity(generated_answer, max_source_id=sources_count)
        no_ev_acc = calculate_no_evidence_refusal(generated_answer, item.should_refuse)
        kw_cov = calculate_keyword_coverage(generated_answer, item.expected_keywords)

        return SingleEvalResult(
            question=item.question,
            expected_doc_ids=item.expected_document_ids,
            retrieved_doc_ids=retrieved_doc_ids,
            generated_answer=generated_answer,
            sources_count=sources_count,
            recall_at_5=rec5,
            precision_at_5=prec5,
            mrr=mrr,
            ndcg_at_5=ndcg5,
            citation_validity=cit_val,
            no_evidence_accuracy=no_ev_acc,
            keyword_coverage=kw_cov,
        )

    async def evaluate_pipeline(
        self,
        query_fn: Callable[[str], Coroutine[Any, Any, tuple[list[str], str, int]]],
    ) -> EvaluationSummary:
        """
        Evaluate full dataset using an async query function:
        query_fn(question) -> (retrieved_doc_ids, generated_answer, sources_count)
        """
        results: list[SingleEvalResult] = []

        for item in self.dataset.items:
            retrieved_doc_ids, generated_answer, sources_count = await query_fn(item.question)
            res = self.evaluate_item(
                item=item,
                retrieved_doc_ids=retrieved_doc_ids,
                generated_answer=generated_answer,
                sources_count=sources_count,
            )
            results.append(res)

        total = len(results)
        if total == 0:
            return EvaluationSummary(
                total_cases=0,
                avg_recall_at_5=0.0,
                avg_precision_at_5=0.0,
                avg_mrr=0.0,
                avg_ndcg_at_5=0.0,
                citation_validity_rate=0.0,
                no_evidence_accuracy_rate=0.0,
                avg_keyword_coverage=0.0,
                results=[],
            )

        summary = EvaluationSummary(
            total_cases=total,
            avg_recall_at_5=sum(r.recall_at_5 for r in results) / total,
            avg_precision_at_5=sum(r.precision_at_5 for r in results) / total,
            avg_mrr=sum(r.mrr for r in results) / total,
            avg_ndcg_at_5=sum(r.ndcg_at_5 for r in results) / total,
            citation_validity_rate=sum(r.citation_validity for r in results) / total,
            no_evidence_accuracy_rate=sum(r.no_evidence_accuracy for r in results) / total,
            avg_keyword_coverage=sum(r.keyword_coverage for r in results) / total,
            results=results,
        )
        return summary
