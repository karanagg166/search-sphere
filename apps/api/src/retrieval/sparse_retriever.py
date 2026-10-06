import time
from typing import Any

import structlog

from src.config import settings
from src.processing.models.search import SparseSearchResult
from src.processing.sparse_embedding.bm25_embedder import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.vector_store.qdrant_store import (
    QdrantVectorStore,
    QdrantVectorStoreError,
)

logger = structlog.get_logger()


class SparseRetrievalError(Exception):
    """Base exception for sparse retrieval operations."""


class SparseQueryValidationError(SparseRetrievalError):
    """Raised when sparse search query or parameters fail validation."""


class SparseRetriever:
    """
    Orchestrates sparse lexical (BM25) retrieval for text queries.

    Responsibilities:
    - Validate query string (reject empty, whitespace-only);
    - Validate top_k parameter (must be > 0 and <= max_top_k);
    - Trim leading/trailing whitespace without altering internal query terms;
    - Compute sparse BM25 query vector using BM25Embedder;
    - Execute sparse search against Qdrant collection using named BM25 sparse vector;
    - Support server-side document_id filtering;
    - Forward optional score_threshold;
    - Return ordered list of domain SparseSearchResult objects;
    - Instrument retrieval timings (embed latency, search latency, total latency);
    - Structured error propagation.
    """

    def __init__(
        self,
        embedder: BM25Embedder | None = None,
        vector_store: QdrantVectorStore | None = None,
        default_top_k: int | None = None,
        max_top_k: int | None = None,
    ) -> None:
        self.embedder = embedder or BM25Embedder()
        self.vector_store = vector_store or QdrantVectorStore()
        self.default_top_k = (
            default_top_k
            if default_top_k is not None
            else getattr(settings, "SPARSE_SEARCH_TOP_K", 10)
        )
        self.max_top_k = (
            max_top_k
            if max_top_k is not None
            else getattr(settings, "SPARSE_SEARCH_MAX_TOP_K", 100)
        )

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        document_id: str | None = None,
        score_threshold: float | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[SparseSearchResult]:
        """
        Retrieve ordered Top-K lexical BM25 relevant chunks for a user query.

        Args:
            query: User search query string.
            top_k: Optional number of top results to retrieve
                (defaults to SPARSE_SEARCH_TOP_K).
            document_id: Optional document ID to filter chunks server-side.
            score_threshold: Optional minimum similarity score threshold.
            filters: Optional metadata filters dictionary.

        Returns:
            Ordered list of SparseSearchResult items preserving Qdrant ranking.

        Raises:
            SparseQueryValidationError: If query is empty/whitespace, top_k is invalid,
                or document_id is malformed.
            SparseRetrievalError: If sparse embedding or vector search fails.
        """
        # 1. Query validation
        if not isinstance(query, str) or not query.strip():
            raise SparseQueryValidationError(
                "Search query must be a non-empty, non-whitespace string."
            )
        clean_query = query.strip()

        # 2. top_k validation
        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if not isinstance(resolved_top_k, int) or resolved_top_k <= 0:
            raise SparseQueryValidationError(
                f"top_k must be a positive integer, got {resolved_top_k}."
            )
        if self.max_top_k is not None and resolved_top_k > self.max_top_k:
            raise SparseQueryValidationError(
                f"top_k ({resolved_top_k}) exceeds maximum allowed "
                f"limit of {self.max_top_k}."
            )

        # 3. document_id validation
        resolved_doc_id: str | None = None
        if document_id is not None:
            if not isinstance(document_id, str) or not document_id.strip():
                raise SparseQueryValidationError(
                    "document_id filter must be a non-empty string if provided."
                )
            resolved_doc_id = document_id.strip()

        start_total = time.perf_counter()

        # 4. Sparse BM25 query embedding
        start_embed = time.perf_counter()
        try:
            query_vector = self.embedder.embed_query(clean_query)
        except SparseEmbeddingError as exc:
            logger.exception(
                "Sparse query embedding failed",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise SparseRetrievalError(
                f"Failed to generate sparse query embedding: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during sparse query embedding",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise SparseRetrievalError(
                f"Unexpected error during sparse query embedding: {exc}"
            ) from exc
        embed_duration_ms = (time.perf_counter() - start_embed) * 1000

        # If query produced no active token indices (e.g. stopwords only)
        if not query_vector.indices:
            logger.info(
                "Sparse query produced no active tokens; returning empty result set",
                query_length=len(clean_query),
            )
            return []

        # 5. Sparse search in Qdrant
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
            results = await self.vector_store.search_sparse(**search_kwargs)
        except QdrantVectorStoreError as exc:
            logger.exception(
                "Qdrant sparse search failed",
                collection=getattr(self.vector_store, "collection_name", "unknown"),
                sparse_vector_name=getattr(
                    self.vector_store, "sparse_vector_name", "unknown"
                ),
                top_k=resolved_top_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise SparseRetrievalError(
                f"Sparse vector search execution failed: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during Qdrant sparse search",
                collection=getattr(self.vector_store, "collection_name", "unknown"),
                top_k=resolved_top_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise SparseRetrievalError(
                f"Unexpected error during sparse search: {exc}"
            ) from exc
        search_duration_ms = (time.perf_counter() - start_search) * 1000

        total_duration_ms = (time.perf_counter() - start_total) * 1000

        logger.info(
            "Sparse BM25 retrieval completed",
            query_length=len(clean_query),
            top_k=resolved_top_k,
            has_document_filter=resolved_doc_id is not None,
            result_count=len(results),
            embed_duration_ms=round(embed_duration_ms, 2),
            search_duration_ms=round(search_duration_ms, 2),
            total_duration_ms=round(total_duration_ms, 2),
        )

        return results
