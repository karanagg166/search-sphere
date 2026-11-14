"""Dependency-injected hybrid RAG retrieval engine."""

import asyncio
import time
from collections.abc import Mapping
from typing import Any

from .contracts import (
    LexicalRetrieverPort,
    ProviderCallError,
    RerankedResult,
    RetrievalCandidate,
    RetrievalResponse,
    RetrievalValidationError,
    VectorRetrieverPort,
)
from .embeddings import EmbeddingProvider
from .fusion import deduplicate_candidates, reciprocal_rank_fusion
from .reranking import HybridFallbackReranker, RerankerProvider


class HybridRagEngine:
    """Orchestrates dense + sparse retrieval, RRF fusion, deduplication, and optional reranking."""

    def __init__(
        self,
        *,
        vector_retriever: VectorRetrieverPort,
        lexical_retriever: LexicalRetrieverPort,
        embedding_provider: EmbeddingProvider | None = None,
        reranker: RerankerProvider | None = None,
        default_top_k: int = 6,
        default_candidate_k: int = 20,
        max_top_k: int = 50,
    ) -> None:
        self.vector_retriever = vector_retriever
        self.lexical_retriever = lexical_retriever
        self.embedding_provider = embedding_provider
        self.reranker = reranker
        self.fallback_reranker = HybridFallbackReranker()
        self.default_top_k = default_top_k
        self.default_candidate_k = default_candidate_k
        self.max_top_k = max_top_k

    async def retrieve(
        self,
        query: str,
        *,
        scope: Mapping[str, Any],
        top_k: int | None = None,
        candidate_k: int | None = None,
        use_reranker: bool = True,
        deduplicate: bool = True,
    ) -> RetrievalResponse:
        clean_query = query.strip() if isinstance(query, str) else ""
        if not clean_query:
            raise RetrievalValidationError("Search query must be a non-empty string.")

        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if resolved_top_k <= 0 or resolved_top_k > self.max_top_k:
            raise RetrievalValidationError(
                f"top_k must be between 1 and {self.max_top_k}, got {resolved_top_k}."
            )

        resolved_candidate_k = candidate_k if candidate_k is not None else max(self.default_candidate_k, resolved_top_k * 3)
        if resolved_candidate_k < resolved_top_k:
            raise RetrievalValidationError("candidate_k cannot be less than top_k.")

        started = time.monotonic()
        dense_candidates: list[RetrievalCandidate] = []
        embedding_space = self.embedding_provider.space if self.embedding_provider else None

        if self.embedding_provider is not None:
            try:
                emb = await self.embedding_provider.embed_query(clean_query)
                emb.space.assert_compatible(self.embedding_provider.space)
                dense_task = self.vector_retriever.search_dense(
                    emb.vectors[0],
                    space=emb.space,
                    scope=scope,
                    limit=resolved_candidate_k,
                )
                sparse_task = self.lexical_retriever.search_lexical(
                    clean_query,
                    scope=scope,
                    limit=resolved_candidate_k,
                )
                dense_candidates, sparse_candidates = await asyncio.gather(dense_task, sparse_task)
            except ProviderCallError:
                sparse_candidates = await self.lexical_retriever.search_lexical(
                    clean_query,
                    scope=scope,
                    limit=resolved_candidate_k,
                )
        else:
            sparse_candidates = await self.lexical_retriever.search_lexical(
                clean_query,
                scope=scope,
                limit=resolved_candidate_k,
            )

        fused = reciprocal_rank_fusion(
            dense_candidates,
            sparse_candidates,
            top_k=resolved_candidate_k,
        )
        if deduplicate and fused:
            fused = deduplicate_candidates(fused)

        strategy = "hybrid" if dense_candidates else "sparse"
        reranked = False
        reranker_model: str | None = None
        results: list[RerankedResult]

        if use_reranker and self.reranker is not None and fused:
            try:
                results = await self.reranker.rerank(clean_query, fused, top_k=resolved_top_k)
                reranked = True
                reranker_model = self.reranker.model_name
            except ProviderCallError:
                results = await self.fallback_reranker.rerank(clean_query, fused, top_k=resolved_top_k)
                reranker_model = self.fallback_reranker.model_name
        else:
            results = await self.fallback_reranker.rerank(clean_query, fused, top_k=resolved_top_k)
            reranker_model = self.fallback_reranker.model_name

        duration_ms = int((time.monotonic() - started) * 1000)
        return RetrievalResponse(
            query=clean_query,
            strategy=strategy,
            embedding_space=embedding_space,
            candidate_count=len({c.chunk_id for c in [*dense_candidates, *sparse_candidates]}),
            reranked=reranked,
            reranker_model=reranker_model,
            duration_ms=duration_ms,
            results=results,
        )
