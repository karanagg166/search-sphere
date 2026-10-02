import time
from typing import Any

import structlog

from src.config import settings
from src.processing.models.search import RerankedSearchResult
from src.retrieval.hybrid_retriever import (
    HybridQueryValidationError,
    HybridRetrievalError,
    HybridRetriever,
)
from src.retrieval.reranker import (
    CrossEncoderReranker,
    RerankingError,
    RerankingValidationError,
)

logger = structlog.get_logger()


class RerankedRetrievalError(Exception):
    """Base exception for reranked hybrid retrieval operations."""


class RerankedQueryValidationError(RerankedRetrievalError):
    """Raised when search query or parameters fail validation."""


class RerankedHybridRetriever:
    """
    Orchestrates two-stage retrieval combining HybridRetriever (Dense + Sparse BM25
    fused via server-side Qdrant RRF) with CrossEncoderReranker.

    Flow:
    1. Validate query string (reject empty, whitespace-only);
    2. Validate top_k and candidate_k (must be positive, candidate_k >= top_k);
    3. Trim query string;
    4. Retrieve top candidate_k chunks via HybridRetriever;
    5. Pass candidate chunks to CrossEncoderReranker;
    6. Return final top_k results sorted by rerank_score descending;
    7. Preserve original RRF scores alongside cross-encoder scores;
    8. Log timing and telemetry across both retrieval stages.
    """

    def __init__(
        self,
        hybrid_retriever: HybridRetriever | None = None,
        reranker: CrossEncoderReranker | None = None,
        default_top_k: int | None = None,
        default_candidate_k: int | None = None,
        max_top_k: int | None = None,
    ) -> None:
        self.hybrid_retriever = hybrid_retriever or HybridRetriever()
        self.reranker = reranker or CrossEncoderReranker()
        self.default_top_k = (
            default_top_k
            if default_top_k is not None
            else getattr(settings, "RERANKER_TOP_K", 5)
        )
        self.default_candidate_k = (
            default_candidate_k
            if default_candidate_k is not None
            else getattr(settings, "HYBRID_SEARCH_CANDIDATE_K", 20)
        )
        self.max_top_k = (
            max_top_k
            if max_top_k is not None
            else getattr(settings, "RERANKER_MAX_TOP_K", 100)
        )

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_id: str | None = None,
        document_ids: list[str] | None = None,
    ) -> list[RerankedSearchResult]:
        """
        Execute two-stage hybrid retrieval followed by cross-encoder reranking.

        Args:
            query: User search query string.
            top_k: Optional final number of reranked results to return.
                Defaults to RERANKER_TOP_K.
            candidate_k: Optional candidate count retrieved by hybrid stage.
                Defaults to HYBRID_SEARCH_CANDIDATE_K.
            document_id: Optional document ID to filter chunks server-side.
            document_ids: Optional list of document IDs to filter chunks server-side.

        Returns:
            Ordered list of RerankedSearchResult items sorted by rerank_score
            descending.

        Raises:
            RerankedQueryValidationError: If query, top_k, candidate_k, or
                document_id fails validation.
            RerankedRetrievalError: If hybrid retrieval or cross-encoder scoring fails.
        """
        # 1. Query validation
        if not isinstance(query, str) or not query.strip():
            raise RerankedQueryValidationError(
                "Search query must be a non-empty, non-whitespace string."
            )
        clean_query = query.strip()

        # 2. top_k validation
        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if (
            not isinstance(resolved_top_k, int)
            or isinstance(resolved_top_k, bool)
            or resolved_top_k <= 0
        ):
            raise RerankedQueryValidationError(
                f"top_k must be a positive integer, got {resolved_top_k}."
            )
        if self.max_top_k is not None and resolved_top_k > self.max_top_k:
            raise RerankedQueryValidationError(
                f"top_k ({resolved_top_k}) exceeds maximum allowed "
                f"limit of {self.max_top_k}."
            )

        # 3. candidate_k validation
        resolved_candidate_k = (
            candidate_k if candidate_k is not None else self.default_candidate_k
        )
        if (
            not isinstance(resolved_candidate_k, int)
            or isinstance(resolved_candidate_k, bool)
            or resolved_candidate_k <= 0
        ):
            raise RerankedQueryValidationError(
                f"candidate_k must be a positive integer, got {resolved_candidate_k}."
            )
        if resolved_candidate_k < resolved_top_k:
            raise RerankedQueryValidationError(
                f"candidate_k ({resolved_candidate_k}) cannot be less than "
                f"top_k ({resolved_top_k})."
            )

        # 4. document_id / document_ids validation
        resolved_doc_id: str | None = None
        if document_id is not None:
            if not isinstance(document_id, str) or not document_id.strip():
                raise RerankedQueryValidationError(
                    "document_id filter must be a non-empty string if provided."
                )
            resolved_doc_id = document_id.strip()

        resolved_doc_ids: list[str] | None = None
        if document_ids is not None:
            if not isinstance(document_ids, (list, tuple)):
                raise RerankedQueryValidationError(
                    "document_ids filter must be a list of strings if provided."
                )
            if len(document_ids) == 0:
                return []
            cleaned_ids = []
            for did in document_ids:
                if not isinstance(did, str) or not did.strip():
                    raise RerankedQueryValidationError(
                        "Each item in document_ids must be a non-empty string."
                    )
                cleaned_ids.append(did.strip())
            resolved_doc_ids = cleaned_ids

        start_total = time.perf_counter()

        # 5. Hybrid retrieval stage (fetch top candidate_k chunks)
        start_hybrid = time.perf_counter()
        try:
            search_kwargs: dict[str, Any] = {
                "query": clean_query,
                "top_k": resolved_candidate_k,
                "candidate_k": resolved_candidate_k,
                "document_id": resolved_doc_id,
            }
            if resolved_doc_ids is not None:
                search_kwargs["document_ids"] = resolved_doc_ids
            candidates = await self.hybrid_retriever.search(**search_kwargs)
        except (HybridRetrievalError, HybridQueryValidationError) as exc:
            logger.exception(
                "Hybrid retrieval failed during two-stage search",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise RerankedRetrievalError(
                f"Hybrid retrieval stage failed: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during hybrid retrieval stage",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise RerankedRetrievalError(
                f"Unexpected error in hybrid retrieval stage: {exc}"
            ) from exc
        hybrid_duration_ms = (time.perf_counter() - start_hybrid) * 1000

        # Return empty list cleanly if hybrid search yielded no candidates
        if not candidates:
            total_duration_ms = (time.perf_counter() - start_total) * 1000
            logger.info(
                "Two-stage retrieval completed with 0 candidates",
                query_length=len(clean_query),
                candidate_k=resolved_candidate_k,
                top_k=resolved_top_k,
                total_latency_ms=round(total_duration_ms, 2),
            )
            return []

        # 6. Cross-Encoder reranking stage
        start_rerank = time.perf_counter()
        try:
            results = await self.reranker.arerank(
                query=clean_query,
                candidates=candidates,
                top_k=resolved_top_k,
            )
        except (RerankingError, RerankingValidationError) as exc:
            logger.exception(
                "Cross-encoder reranking failed during two-stage search",
                candidate_count=len(candidates),
                top_k=resolved_top_k,
                error=str(exc),
            )
            raise RerankedRetrievalError(
                f"Cross-encoder reranking stage failed: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during cross-encoder reranking stage",
                candidate_count=len(candidates),
                top_k=resolved_top_k,
                error=str(exc),
            )
            raise RerankedRetrievalError(
                f"Unexpected error in reranking stage: {exc}"
            ) from exc
        rerank_duration_ms = (time.perf_counter() - start_rerank) * 1000
        total_duration_ms = (time.perf_counter() - start_total) * 1000

        logger.info(
            "Two-stage retrieval completed",
            query_length=len(clean_query),
            candidates_retrieved=len(candidates),
            final_count=len(results),
            hybrid_latency_ms=round(hybrid_duration_ms, 2),
            rerank_latency_ms=round(rerank_duration_ms, 2),
            total_latency_ms=round(total_duration_ms, 2),
        )

        return results
