import math
import re
from typing import Sequence


def calculate_recall_at_k(
    retrieved: Sequence[str], relevant: Sequence[str], k: int
) -> float:
    """
    Calculate Recall@K: fraction of ground-truth relevant documents retrieved in the top K.
    """
    if not relevant:
        return 1.0

    retrieved_k = set(retrieved[:k])
    relevant_set = set(relevant)
    hits = len(retrieved_k & relevant_set)
    return hits / len(relevant_set)


def calculate_precision_at_k(
    retrieved: Sequence[str], relevant: Sequence[str], k: int
) -> float:
    """
    Calculate Precision@K: fraction of top K retrieved documents that are relevant.
    """
    if k <= 0:
        return 0.0

    retrieved_k = retrieved[:k]
    if not retrieved_k:
        return 0.0

    relevant_set = set(relevant)
    hits = sum(1 for item in retrieved_k if item in relevant_set)
    return hits / len(retrieved_k)


def calculate_mrr(retrieved: Sequence[str], relevant: Sequence[str]) -> float:
    """
    Calculate Mean Reciprocal Rank (MRR): reciprocal of the rank of the first relevant document.
    """
    if not relevant:
        return 1.0

    relevant_set = set(relevant)
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


def calculate_ndcg_at_k(
    retrieved: Sequence[str], relevant: Sequence[str], k: int
) -> float:
    """
    Calculate Normalized Discounted Cumulative Gain at K (nDCG@K) with binary relevance.
    """
    if not relevant or k <= 0:
        return 1.0

    retrieved_k = retrieved[:k]
    relevant_set = set(relevant)

    # Discounted Cumulative Gain (DCG)
    dcg = 0.0
    for i, doc_id in enumerate(retrieved_k, start=1):
        if doc_id in relevant_set:
            dcg += 1.0 / math.log2(i + 1)

    # Ideal Discounted Cumulative Gain (IDCG)
    ideal_hits = min(len(relevant_set), k)
    if ideal_hits == 0:
        return 1.0

    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def calculate_citation_validity(answer: str, max_source_id: int) -> float:
    """
    Check if all [N] citation markers in the answer are valid references (1 <= N <= max_source_id).
    Returns 1.0 if all citations are valid, 0.0 if hallucinated citations exist.
    """
    citations = re.findall(r"\[(\d+)\]", answer)
    if not citations:
        return 1.0

    if max_source_id <= 0:
        return 0.0

    for cit in citations:
        source_num = int(cit)
        if source_num < 1 or source_num > max_source_id:
            return 0.0

    return 1.0


def calculate_no_evidence_refusal(answer: str, should_refuse: bool) -> float:
    """
    Evaluate whether the system correctly refuses when ground truth evidence is absent.
    """
    refusal_phrases = [
        "couldn't find",
        "do not contain enough information",
        "does not contain enough information",
        "not enough information",
        "no relevant information",
        "cannot be answered",
        "insufficient evidence",
    ]
    lower_answer = answer.lower()
    is_refusal = any(phrase in lower_answer for phrase in refusal_phrases)

    if should_refuse:
        return 1.0 if is_refusal else 0.0
    else:
        return 0.0 if is_refusal else 1.0


def calculate_keyword_coverage(answer: str, expected_keywords: Sequence[str]) -> float:
    """
    Fraction of expected keywords present in the generated answer.
    """
    if not expected_keywords:
        return 1.0

    lower_answer = answer.lower()
    hits = sum(1 for kw in expected_keywords if kw.lower() in lower_answer)
    return hits / len(expected_keywords)
