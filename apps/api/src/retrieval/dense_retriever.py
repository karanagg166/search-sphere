import time

import structlog

from src.config import settings
from src.processing.embedding.dense_embedder import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.models.search import DenseSearchResult
from src.vector_store.qdrant_store import (
    QdrantVectorStore,
    QdrantVectorStoreError,
)

logger = structlog.get_logger()


class DenseRetrievalError(Exception):
    """Base exception for dense retrieval operations."""


class QueryValidationError(DenseRetrievalError):
    """Raised when search query or parameters fail validation."""


RetrievalError = DenseRetrievalError


class DenseRetriever:
    """
    Orchestrates dense ANN retrieval for text queries.

    Responsibilities:
    - Validate query string (reject empty, whitespace-only);
    - Validate top_k parameter (must be > 0 and <= max_top_k);
    - Trim leading/trailing whitespace without altering internal query terms;
    - Compute dense query vector using existing DenseEmbedder;
    - Execute ANN search against Qdrant collection;
    - Support server-side document_id filtering;
    - Forward optional score_threshold;
    - Return ordered list of domain DenseSearchResult objects;
    - Instrument retrieval timings (embed latency, search latency, total latency);
    - Structured error propagation.
    """

    def __init__(
        self,
        embedder: DenseEmbedder | None = None,
        vector_store: QdrantVectorStore | None = None,
        default_top_k: int | None = None,
        max_top_k: int | None = None,
    ) -> None:
        self.embedder = embedder or DenseEmbedder()
        self.vector_store = vector_store or QdrantVectorStore()
        self.default_top_k = (
            default_top_k
            if default_top_k is not None
            else getattr(settings, "DENSE_SEARCH_TOP_K", 10)
        )
        self.max_top_k = (
            max_top_k
            if max_top_k is not None
            else getattr(settings, "DENSE_SEARCH_MAX_TOP_K", 100)
        )

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        document_id: str | None = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[DenseSearchResult]:
        """
        Retrieve ordered Top-K relevant chunks for a user query.

        Args:
            query: User search query string.
            top_k: Optional number of top results to retrieve
                (defaults to DENSE_SEARCH_TOP_K).
            document_id: Optional document ID to filter chunks server-side.
            score_threshold: Optional minimum similarity score threshold.
            filters: Optional metadata filters dictionary.

        Returns:
            Ordered list of DenseSearchResult items preserving Qdrant ranking.

        Raises:
            QueryValidationError: If query is empty/whitespace, top_k is invalid,
                or document_id is malformed.
            DenseRetrievalError: If embedding or vector search fails.
        """
        # 1. Query validation
        if not isinstance(query, str) or not query.strip():
            raise QueryValidationError(
                "Search query must be a non-empty, non-whitespace string."
            )
        clean_query = query.strip()

        # 2. top_k validation
        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if not isinstance(resolved_top_k, int) or resolved_top_k <= 0:
            raise QueryValidationError(
                f"top_k must be a positive integer, got {resolved_top_k}."
            )
        if self.max_top_k is not None and resolved_top_k > self.max_top_k:
            raise QueryValidationError(
                f"top_k ({resolved_top_k}) exceeds maximum allowed "
                f"limit of {self.max_top_k}."
            )

        # 3. document_id validation
        resolved_doc_id: str | None = None
        if document_id is not None:
            if not isinstance(document_id, str) or not document_id.strip():
                raise QueryValidationError(
                    "document_id filter must be a non-empty string if provided."
                )
            resolved_doc_id = document_id.strip()

        start_total = time.perf_counter()

        # 4. Dense query embedding
        start_embed = time.perf_counter()
        try:
            vectors = self.embedder.embed_texts([clean_query])
        except DenseEmbeddingError as exc:
            logger.exception(
                "Dense query embedding failed",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise DenseRetrievalError(
                f"Failed to generate query embedding: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during query embedding",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise DenseRetrievalError(
                f"Unexpected error during query embedding: {exc}"
            ) from exc
        embed_duration_ms = (time.perf_counter() - start_embed) * 1000

        if not vectors or len(vectors) != 1:
            got_count = len(vectors) if vectors else 0
            raise DenseRetrievalError(
                f"Expected exactly 1 query vector from embedder, got {got_count}."
            )
        query_vector = vectors[0]

        # 5. Qdrant ANN search
        start_search = time.perf_counter()
        try:
            search_kwargs: dict[str, Any] = {
                "query_vector": query_vector,
                "limit": resolved_top_k,
                "document_id": resolved_doc_id,
                "score_threshold": score_threshold,
            }
            if filters is not None:
                search_kwargs["filters"] = filters
            results = await self.vector_store.search_dense(**search_kwargs)
        except QdrantVectorStoreError as exc:
            logger.exception(
                "Qdrant dense search failed",
                top_k=resolved_top_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise DenseRetrievalError(f"Dense vector search failed: {exc}") from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during dense search",
                top_k=resolved_top_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise DenseRetrievalError(
                f"Unexpected error during dense search: {exc}"
            ) from exc
        search_duration_ms = (time.perf_counter() - start_search) * 1000
        total_duration_ms = (time.perf_counter() - start_total) * 1000

        logger.info(
            "Dense retrieval completed",
            query_length=len(clean_query),
            top_k=resolved_top_k,
            has_document_filter=resolved_doc_id is not None,
            result_count=len(results),
            embed_latency_ms=round(embed_duration_ms, 2),
            search_latency_ms=round(search_duration_ms, 2),
            total_latency_ms=round(total_duration_ms, 2),
        )

        return results
