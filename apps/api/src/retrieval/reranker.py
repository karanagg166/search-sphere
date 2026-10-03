import math
import os
import time
from collections.abc import Sequence
from typing import Any

import structlog

from src.config import settings
from src.processing.models.search import RerankedSearchResult

logger = structlog.get_logger()


class RerankingError(Exception):
    """Base exception for cross-encoder reranking operations."""


class RerankingValidationError(RerankingError):
    """Raised when query, candidates, or parameters fail validation."""


# Aliases for naming flexibility
RerankerError = RerankingError
RerankerValidationError = RerankingValidationError


class CrossEncoderReranker:
    """
    Reranks retrieved candidate document chunks using a Cross-Encoder model.

    Responsibilities:
    - Validate query string (reject empty, whitespace-only, non-string);
    - Validate candidates sequence (reject non-sequence, items without content);
    - Validate top_k parameter (must be positive integer, <= max_top_k);
    - Form (query, text) pairs for candidate chunks;
    - Score pairs in batches using a SentenceTransformers CrossEncoder model;
    - Strictly validate output scores (count match, non-empty, no NaN/Infinity);
    - Sort candidates descending by rerank score;
    - Preserve candidate metadata & earlier RRF score in RerankedSearchResult;
    - Assign 1-indexed rank positions;
    - Log structured timing and metric telemetry.
    """

    _cached_model: Any = None
    _cached_model_name: str | None = None

    def __init__(
        self,
        model_name: str | None = None,
        batch_size: int | None = None,
        model: Any = None,
        device: str | None = None,
        default_top_k: int | None = None,
        max_top_k: int | None = None,
    ) -> None:
        self.model_name = model_name or getattr(
            settings, "RERANKER_MODEL_NAME", "cross-encoder/ms-marco-MiniLM-L-6-v2"
        )
        self.batch_size = (
            batch_size
            if batch_size is not None
            else getattr(settings, "RERANKER_BATCH_SIZE", 32)
        )
        self.device = device or getattr(settings, "RERANKER_DEVICE", "cpu")
        self.default_top_k = (
            default_top_k
            if default_top_k is not None
            else getattr(settings, "RERANKER_TOP_K", 5)
        )
        self.max_top_k = (
            max_top_k
            if max_top_k is not None
            else getattr(settings, "RERANKER_MAX_TOP_K", 100)
        )
        self._model = model

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        top_k: int | None = None,
    ) -> list[RerankedSearchResult]:
        """
        Rerank candidate document chunks against the user query.

        Args:
            query: User search query string.
            candidates: Sequence of retrieved candidate chunks (e.g.
                HybridSearchResult).
            top_k: Optional number of top results to return.
                Defaults to RERANKER_TOP_K.

        Returns:
            List of RerankedSearchResult items sorted by rerank_score descending,
            with ranks 1..K assigned.

        Raises:
            RerankingValidationError: If query, candidates, or top_k fail validation.
            RerankingError: If cross-encoder model scoring or output validation fails.
        """
        # 1. Query validation
        if not isinstance(query, str) or not query.strip():
            raise RerankingValidationError(
                "Search query must be a non-empty, non-whitespace string."
            )
        clean_query = query.strip()

        # 2. Candidates validation
        if candidates is None or not isinstance(candidates, (list, tuple)):
            raise RerankingValidationError(
                "Candidates must be a list or sequence of candidate items."
            )

        # 3. top_k validation
        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if (
            not isinstance(resolved_top_k, int)
            or isinstance(resolved_top_k, bool)
            or resolved_top_k <= 0
        ):
            raise RerankingValidationError(
                f"top_k must be a positive integer, got {resolved_top_k}."
            )
        if self.max_top_k is not None and resolved_top_k > self.max_top_k:
            raise RerankingValidationError(
                f"top_k ({resolved_top_k}) exceeds maximum allowed "
                f"limit of {self.max_top_k}."
            )

        # Clean early return when candidate list is empty
        if len(candidates) == 0:
            return []

        start_total = time.perf_counter()

        # 4. Extract (query, content) pairs
        pairs: list[tuple[str, str]] = []
        for idx, candidate in enumerate(candidates):
            content: Any = None
            if hasattr(candidate, "content"):
                content = candidate.content
            elif isinstance(candidate, dict):
                content = candidate.get("content")

            if not isinstance(content, str):
                raise RerankingValidationError(
                    f"Candidate at index {idx} does not contain valid text content: "
                    f"{content!r}"
                )
            pairs.append((clean_query, content))

        # 5. Score candidate pairs
        start_score = time.perf_counter()
        try:
            scores = self._score_pairs(pairs)
            score_duration_ms = (time.perf_counter() - start_score) * 1000

            # 6. Sort descending by rerank score
            scored_candidates = list(zip(candidates, scores, strict=True))
            # Stable sort preserves relative order for identical scores
            sorted_candidates = sorted(
                scored_candidates,
                key=lambda item: item[1],
                reverse=True,
            )
            top_candidates = sorted_candidates[:resolved_top_k]
        except Exception as exc:
            score_duration_ms = (time.perf_counter() - start_score) * 1000
            logger.warning(
                "Cross-encoder scoring failed or model unavailable; falling back to RRF retriever ranking",
                query=clean_query,
                error=str(exc),
            )

            def _get_fallback_score(cand_item: Any) -> float:
                if hasattr(cand_item, "rrf_score") and cand_item.rrf_score is not None:
                    return float(cand_item.rrf_score)
                if hasattr(cand_item, "score") and cand_item.score is not None:
                    return float(cand_item.score)
                if isinstance(cand_item, dict):
                    raw = cand_item.get("rrf_score", cand_item.get("score"))
                    if raw is not None:
                        return float(raw)
                return 0.0

            sorted_by_rrf = sorted(
                [(c, _get_fallback_score(c)) for c in candidates],
                key=lambda item: item[1],
                reverse=True,
            )
            top_candidates = sorted_by_rrf[:resolved_top_k]

        # 7. Build RerankedSearchResult objects
        results: list[RerankedSearchResult] = []
        for rank_idx, (cand, score) in enumerate(top_candidates, start=1):
            rrf_score: float | None = None
            if hasattr(cand, "rrf_score") and cand.rrf_score is not None:
                rrf_score = float(cand.rrf_score)
            elif hasattr(cand, "score") and cand.score is not None:
                rrf_score = float(cand.score)
            elif isinstance(cand, dict):
                raw_rrf = cand.get("rrf_score", cand.get("score"))
                if raw_rrf is not None:
                    rrf_score = float(raw_rrf)

            point_id = (
                getattr(cand, "point_id", None)
                or (cand.get("point_id") if isinstance(cand, dict) else "")
                or ""
            )
            document_id = (
                getattr(cand, "document_id", None)
                or (cand.get("document_id") if isinstance(cand, dict) else "")
                or ""
            )
            chunk_index = (
                getattr(cand, "chunk_index", 0)
                if hasattr(cand, "chunk_index")
                else (cand.get("chunk_index", 0) if isinstance(cand, dict) else 0)
            )
            content = (
                getattr(cand, "content", "")
                if hasattr(cand, "content")
                else (cand.get("content", "") if isinstance(cand, dict) else "")
            )
            token_count = (
                getattr(cand, "token_count", 0)
                if hasattr(cand, "token_count")
                else (cand.get("token_count", 0) if isinstance(cand, dict) else 0)
            )
            start_page = (
                getattr(cand, "start_page", 1)
                if hasattr(cand, "start_page")
                else (cand.get("start_page", 1) if isinstance(cand, dict) else 1)
            )
            end_page = (
                getattr(cand, "end_page", 1)
                if hasattr(cand, "end_page")
                else (cand.get("end_page", 1) if isinstance(cand, dict) else 1)
            )
            page_numbers = (
                list(getattr(cand, "page_numbers", [1]))
                if hasattr(cand, "page_numbers")
                else (
                    list(cand.get("page_numbers", [1]))
                    if isinstance(cand, dict)
                    else [1]
                )
            )
            block_types = (
                list(getattr(cand, "block_types", ["text"]))
                if hasattr(cand, "block_types")
                else (
                    list(cand.get("block_types", ["text"]))
                    if isinstance(cand, dict)
                    else ["text"]
                )
            )

            results.append(
                RerankedSearchResult(
                    point_id=point_id,
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=content,
                    token_count=token_count,
                    start_page=start_page,
                    end_page=end_page,
                    page_numbers=page_numbers,
                    block_types=block_types,
                    rerank_score=score,
                    rrf_score=rrf_score,
                    score=score,
                    rank=rank_idx,
                )
            )

        total_duration_ms = (time.perf_counter() - start_total) * 1000

        logger.info(
            "Cross-encoder reranking completed",
            query_length=len(clean_query),
            candidate_count=len(candidates),
            top_k=resolved_top_k,
            result_count=len(results),
            score_latency_ms=round(score_duration_ms, 2),
            total_latency_ms=round(total_duration_ms, 2),
        )

        return results

    async def arerank(
        self,
        query: str,
        candidates: Sequence[Any],
        top_k: int | None = None,
    ) -> list[RerankedSearchResult]:
        """
        Asynchronously rerank candidates by executing the model inference in a
        worker thread pool to avoid blocking the event loop.
        """
        import asyncio

        return await asyncio.to_thread(self.rerank, query, candidates, top_k)

    def _score_pairs(self, pairs: list[tuple[str, str]]) -> list[float]:
        """Run batched model inference and validate returned scores."""
        model = self._get_model()

        try:
            if hasattr(model, "rerank"):
                query = pairs[0][0]
                documents = [p[1] for p in pairs]
                raw_scores = list(model.rerank(query, documents))
            elif hasattr(model, "predict"):
                raw_scores = model.predict(
                    pairs,
                    batch_size=self.batch_size,
                    show_progress_bar=False,
                )
            elif callable(model):
                raw_scores = model(pairs)
            else:
                raise RerankingError(
                    f"Model of type {type(model)} does not provide a 'predict' or 'rerank' "
                    f"method and is not callable."
                )
        except RerankingError:
            raise
        except Exception as exc:
            logger.exception(
                "Cross-encoder scoring inference failed",
                model_name=self.model_name,
                pair_count=len(pairs),
                error=str(exc),
            )
            raise RerankingError(
                f"Failed to score candidates with cross-encoder model "
                f"'{self.model_name}': {exc}"
            ) from exc

        # Normalize output to list of numeric values
        if hasattr(raw_scores, "tolist"):
            scores_list = raw_scores.tolist()
        elif isinstance(raw_scores, (int, float)):
            scores_list = [raw_scores]
        elif isinstance(raw_scores, (list, tuple)):
            scores_list = [
                s.tolist() if hasattr(s, "tolist")
                else float(s) if isinstance(s, (int, float))
                else s
                for s in raw_scores
            ]
        else:
            raise RerankingError(
                f"Unexpected cross-encoder output type: {type(raw_scores)}"
            )

        if not isinstance(scores_list, list):
            scores_list = [scores_list]

        if len(scores_list) != len(pairs):
            raise RerankingError(
                f"Score count mismatch: expected {len(pairs)} scores for "
                f"{len(pairs)} candidates, but received {len(scores_list)}."
            )

        converted_scores: list[float] = []
        for idx, s in enumerate(scores_list):
            if isinstance(s, bool) or not isinstance(s, (int, float)):
                raise RerankingError(
                    f"Non-numeric rerank score at candidate {idx}: {s!r}"
                )
            if math.isnan(s):
                raise RerankingError(
                    f"NaN detected in rerank score at candidate {idx}."
                )
            if math.isinf(s):
                raise RerankingError(
                    f"Infinity detected in rerank score at candidate {idx}."
                )
            converted_scores.append(float(s))

        return converted_scores

    def _get_model(self) -> Any:
        """Lazily load and cache the CrossEncoder model (prefers FastEmbed ONNX Runtime for memory safety)."""
        if self._model is not None:
            return self._model

        if (
            CrossEncoderReranker._cached_model is not None
            and CrossEncoderReranker._cached_model_name == self.model_name
        ):
            self._model = CrossEncoderReranker._cached_model
            return self._model

        # 1. Prefer FastEmbed TextCrossEncoder (ONNX Runtime) to avoid PyTorch memory exhaustion
        try:
            from fastembed.rerank.cross_encoder import TextCrossEncoder

            fastembed_model = "Xenova/ms-marco-MiniLM-L-6-v2"
            cache_path = getattr(settings, "FASTEMBED_CACHE_PATH", None) or os.getenv("FASTEMBED_CACHE_PATH")
            logger.info("Loading FastEmbed TextCrossEncoder", model_name=fastembed_model, cache_path=cache_path)
            loaded_model = TextCrossEncoder(model_name=fastembed_model, cache_dir=cache_path)
            CrossEncoderReranker._cached_model = loaded_model
            CrossEncoderReranker._cached_model_name = self.model_name
            self._model = loaded_model
            logger.info("FastEmbed TextCrossEncoder loaded and cached", model_name=fastembed_model)
            return self._model
        except Exception as fe_exc:
            logger.warning(
                "FastEmbed TextCrossEncoder unavailable or failed to load",
                error=str(fe_exc),
            )
            if getattr(settings, "ENVIRONMENT", "").lower() == "production":
                raise RerankingError(
                    f"FastEmbed TextCrossEncoder failed in production: {fe_exc}"
                ) from fe_exc

        # 2. Fallback to SentenceTransformer CrossEncoder
        logger.info(
            "Loading sentence-transformer cross-encoder model",
            model_name=self.model_name,
            device=self.device,
        )

        try:
            from sentence_transformers import CrossEncoder

            loaded_model = CrossEncoder(self.model_name, device=self.device)
            CrossEncoderReranker._cached_model = loaded_model
            CrossEncoderReranker._cached_model_name = self.model_name
            self._model = loaded_model

            logger.info(
                "Cross-encoder model loaded and cached",
                model_name=self.model_name,
                device=self.device,
            )
            return self._model

        except Exception as exc:
            logger.warning(
                "Failed to load sentence-transformer cross-encoder model",
                model_name=self.model_name,
                error=str(exc),
            )
            raise RerankingError(
                f"Failed to load cross-encoder model '{self.model_name}': {exc}"
            ) from exc

    @classmethod
    def _clear_cache(cls) -> None:
        """Clear cached model in memory (useful for testing)."""
        cls._cached_model = None
        cls._cached_model_name = None
