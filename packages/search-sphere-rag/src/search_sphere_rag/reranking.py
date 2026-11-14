"""Hosted reranking with Cohere Rerank v3.5 and deterministic hybrid fallback."""

import math
from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from .contracts import FusedCandidate, ProviderCallError, RerankedResult, RetrievalValidationError


@runtime_checkable
class RerankerProvider(Protocol):
    @property
    def model_name(self) -> str: ...

    async def rerank(
        self,
        query: str,
        candidates: Sequence[FusedCandidate],
        *,
        top_k: int,
    ) -> list[RerankedResult]: ...


class HybridFallbackReranker:
    """Deterministic hybrid-ranking fallback when hosted reranking is unavailable."""

    @property
    def model_name(self) -> str:
        return "hybrid_rrf_fallback"

    async def rerank(
        self,
        query: str,
        candidates: Sequence[FusedCandidate],
        *,
        top_k: int,
    ) -> list[RerankedResult]:
        if not query.strip():
            raise RetrievalValidationError("Query must not be empty.")
        if top_k <= 0:
            raise RetrievalValidationError("top_k must be positive.")

        ordered = sorted(
            candidates,
            key=lambda item: (-item.rrf_score, item.candidate.start_page, item.candidate.chunk_id),
        )[:top_k]
        return [
            RerankedResult(
                candidate=item.candidate,
                dense_score=item.dense_score,
                sparse_score=item.sparse_score,
                hybrid_score=item.rrf_score,
                rerank_score=item.rrf_score,
                final_rank=idx,
                reranked_by=self.model_name,
            )
            for idx, item in enumerate(ordered, start=1)
        ]


class CohereRerankProvider:
    """Hosted Cohere Rerank v3.5 adapter without local heavyweight models."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "rerank-v3.5",
        timeout_seconds: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise RetrievalValidationError("Cohere API key is required for CohereRerankProvider.")
        self._api_key = api_key.strip()
        self._model = model
        self._timeout = timeout_seconds
        self._transport = transport

    @property
    def model_name(self) -> str:
        return self._model

    async def rerank(
        self,
        query: str,
        candidates: Sequence[FusedCandidate],
        *,
        top_k: int,
    ) -> list[RerankedResult]:
        clean_query = query.strip()
        if not clean_query:
            raise RetrievalValidationError("Query must not be empty.")
        if top_k <= 0:
            raise RetrievalValidationError("top_k must be positive.")
        if not candidates:
            return []

        docs = [item.candidate.content for item in candidates]
        bounded_top_n = min(top_k, len(docs))

        async with httpx.AsyncClient(
            base_url="https://api.cohere.com",
            timeout=httpx.Timeout(self._timeout, connect=5.0),
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            try:
                resp = await client.post(
                    "/v2/rerank",
                    json={
                        "model": self._model,
                        "query": clean_query,
                        "documents": docs,
                        "top_n": bounded_top_n,
                    },
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderCallError(f"Cohere rerank request failed: {type(exc).__name__}") from exc

            if resp.status_code != 200:
                raise ProviderCallError(f"Cohere rerank rejected request with HTTP {resp.status_code}.")

            payload: dict[str, Any] = resp.json()

        raw_results = payload.get("results")
        if not isinstance(raw_results, list):
            raise ProviderCallError("Cohere rerank response missing results list.")

        reranked: list[RerankedResult] = []
        seen_indices: set[int] = set()
        for entry in raw_results:
            if not isinstance(entry, dict):
                continue
            idx = entry.get("index")
            score = entry.get("relevance_score")
            if type(idx) is not int or idx < 0 or idx >= len(candidates) or idx in seen_indices:
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise ProviderCallError("Cohere rerank returned non-numeric score.")
            fscore = float(score)
            if math.isnan(fscore) or math.isinf(fscore):
                raise ProviderCallError("Cohere rerank returned non-finite score.")
            seen_indices.add(idx)
            fused = candidates[idx]
            reranked.append(
                RerankedResult(
                    candidate=fused.candidate,
                    dense_score=fused.dense_score,
                    sparse_score=fused.sparse_score,
                    hybrid_score=fused.rrf_score,
                    rerank_score=round(fscore, 6),
                    final_rank=len(reranked) + 1,
                    reranked_by=self._model,
                )
            )

        reranked.sort(key=lambda r: (-r.rerank_score, -r.hybrid_score, r.candidate.chunk_id))
        return [
            item.model_copy(update={"final_rank": rank})
            for rank, item in enumerate(reranked[:bounded_top_n], start=1)
        ]
