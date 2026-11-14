"""Reciprocal Rank Fusion (RRF) and token-overlap chunk deduplication."""

import re
from collections.abc import Sequence
from typing import TypedDict

from .contracts import FusedCandidate, RetrievalCandidate


class _FusionEntry(TypedDict):
    candidate: RetrievalCandidate
    dense_score: float | None
    sparse_score: float | None
    dense_rank: int | None
    sparse_rank: int | None
    rrf_score: float


def _tokenize(text: str) -> set[str]:
    return {tok for tok in re.findall(r"[a-z0-9]+", text.casefold()) if len(tok) > 1}


def reciprocal_rank_fusion(
    dense_candidates: Sequence[RetrievalCandidate],
    sparse_candidates: Sequence[RetrievalCandidate],
    *,
    rrf_k: int = 60,
    dense_weight: float = 1.0,
    sparse_weight: float = 1.0,
    top_k: int = 20,
) -> list[FusedCandidate]:
    """Fuse dense and sparse ranked candidate lists using Reciprocal Rank Fusion."""
    if rrf_k <= 0:
        raise ValueError("rrf_k must be positive")
    if top_k <= 0:
        raise ValueError("top_k must be positive")

    by_id: dict[str, _FusionEntry] = {}

    for rank_idx, item in enumerate(dense_candidates, start=1):
        entry = by_id.setdefault(
            item.chunk_id,
            _FusionEntry(
                candidate=item,
                dense_score=None,
                sparse_score=None,
                dense_rank=None,
                sparse_rank=None,
                rrf_score=0.0,
            ),
        )
        entry["dense_score"] = float(item.score)
        entry["dense_rank"] = rank_idx
        entry["rrf_score"] += dense_weight / (rrf_k + rank_idx)

    for rank_idx, item in enumerate(sparse_candidates, start=1):
        entry = by_id.setdefault(
            item.chunk_id,
            _FusionEntry(
                candidate=item,
                dense_score=None,
                sparse_score=None,
                dense_rank=None,
                sparse_rank=None,
                rrf_score=0.0,
            ),
        )
        entry["sparse_score"] = float(item.score)
        entry["sparse_rank"] = rank_idx
        entry["rrf_score"] += sparse_weight / (rrf_k + rank_idx)

    fused = [
        FusedCandidate(
            candidate=entry["candidate"],
            dense_score=entry["dense_score"],
            sparse_score=entry["sparse_score"],
            dense_rank=entry["dense_rank"],
            sparse_rank=entry["sparse_rank"],
            rrf_score=round(entry["rrf_score"], 8),
        )
        for entry in by_id.values()
    ]
    fused.sort(
        key=lambda item: (
            -item.rrf_score,
            item.candidate.start_page,
            item.candidate.chunk_id,
        )
    )
    selected = fused[:top_k]
    return [item.model_copy(update={"final_rank": idx}) for idx, item in enumerate(selected, start=1)]


def deduplicate_candidates(
    candidates: Sequence[FusedCandidate],
    *,
    similarity_threshold: float = 0.70,
) -> list[FusedCandidate]:
    """Remove near-duplicate chunks while preserving highest-ranked evidence."""
    kept: list[FusedCandidate] = []
    kept_tokens: list[set[str]] = []

    for item in candidates:
        tokens = _tokenize(item.candidate.content)
        is_dup = False
        if tokens:
            for existing_tokens in kept_tokens:
                union = tokens | existing_tokens
                if union and (len(tokens & existing_tokens) / len(union)) >= similarity_threshold:
                    is_dup = True
                    break
        if not is_dup:
            kept.append(item)
            kept_tokens.append(tokens)

    return [item.model_copy(update={"final_rank": idx}) for idx, item in enumerate(kept, start=1)]
