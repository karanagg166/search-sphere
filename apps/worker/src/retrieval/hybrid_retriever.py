import time
from typing import Any

import structlog

from src.config import settings
from src.processing.embedding.dense_embedder import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.models.search import HybridSearchResult
from src.processing.sparse_embedding.bm25_embedder import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.vector_store.qdrant_store import (
    QdrantVectorStore,
    QdrantVectorStoreError,
)

logger = structlog.get_logger()


class HybridRetrievalError(Exception):
    """Base exception for hybrid retrieval operations."""


class HybridQueryValidationError(HybridRetrievalError):
    """Raised when search query or hybrid retrieval parameters fail validation."""


class HybridRetriever:
    """
    Orchestrates hybrid retrieval combining dense semantic embeddings and sparse
    BM25 representations via Qdrant server-side Reciprocal Rank Fusion (RRF).

    Flow:
    1. Validate query string (reject empty, whitespace-only, non-string);
    2. Validate top_k and candidate_k (must be positive, candidate_k >= top_k);
    3. Trim leading/trailing whitespace without altering internal query terms;
    4. Compute dense query embedding vector via DenseEmbedder;
    5. Compute sparse BM25 query vector via BM25Embedder;
    6. Execute single hybrid query in Qdrant with dense and sparse prefetches + RRF;
    7. Support server-side document_id scoping across both branches;
    8. Return ordered list of domain HybridSearchResult items with final RRF ranks;
    9. Log structured retrieval timing metrics;
    10. Structured exception wrapping with HybridRetrievalError.
    """

    def __init__(
        self,
        dense_embedder: DenseEmbedder | None = None,
        sparse_embedder: BM25Embedder | None = None,
        vector_store: QdrantVectorStore | None = None,
        default_top_k: int | None = None,
        default_candidate_k: int | None = None,
        max_top_k: int | None = None,
    ) -> None:
        self.dense_embedder = dense_embedder or DenseEmbedder()
        self.sparse_embedder = sparse_embedder or BM25Embedder()
        self.vector_store = vector_store or QdrantVectorStore()
        self.default_top_k = (
            default_top_k
            if default_top_k is not None
            else getattr(settings, "HYBRID_SEARCH_TOP_K", 10)
        )
        self.default_candidate_k = (
            default_candidate_k
            if default_candidate_k is not None
            else getattr(settings, "HYBRID_SEARCH_CANDIDATE_K", 20)
        )
        self.max_top_k = (
            max_top_k
            if max_top_k is not None
            else getattr(settings, "HYBRID_SEARCH_MAX_TOP_K", 100)
        )

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_id: str | None = None,
    ) -> list[HybridSearchResult]:
        """
        Retrieve ordered Top-K relevant chunks combining dense semantic and sparse BM25
        lexical signals using Reciprocal Rank Fusion (RRF).

        Args:
            query: User search query string.
            top_k: Optional number of fused top results to retrieve.
                Defaults to HYBRID_SEARCH_TOP_K.
            candidate_k: Optional candidate prefetch limit per retriever branch.
                Defaults to HYBRID_SEARCH_CANDIDATE_K.
            document_id: Optional document ID to filter chunks server-side.

        Returns:
            Ordered list of HybridSearchResult items preserving Qdrant RRF ranking.

        Raises:
            HybridQueryValidationError: If query is empty/whitespace, top_k is invalid,
                candidate_k is invalid, candidate_k < top_k, or document_id is
                malformed.
            HybridRetrievalError: If embedding generation or vector search fails.
        """
        # 1. Query validation & trimming
        if not isinstance(query, str) or not query.strip():
            raise HybridQueryValidationError(
                "Search query must be a non-empty, non-whitespace string."
            )
        clean_query = query.strip()

        # 2. top_k validation
        resolved_top_k = top_k if top_k is not None else self.default_top_k
        if not isinstance(resolved_top_k, int) or resolved_top_k <= 0:
            raise HybridQueryValidationError(
                f"top_k must be a positive integer, got {resolved_top_k}."
            )
        if self.max_top_k is not None and resolved_top_k > self.max_top_k:
            raise HybridQueryValidationError(
                f"top_k ({resolved_top_k}) exceeds maximum allowed "
                f"limit of {self.max_top_k}."
            )

        # 3. candidate_k validation
        resolved_candidate_k = (
            candidate_k if candidate_k is not None else self.default_candidate_k
        )
        if not isinstance(resolved_candidate_k, int) or resolved_candidate_k <= 0:
            raise HybridQueryValidationError(
                f"candidate_k must be a positive integer, got {resolved_candidate_k}."
            )
        if resolved_candidate_k < resolved_top_k:
            raise HybridQueryValidationError(
                f"candidate_k ({resolved_candidate_k}) cannot be less than "
                f"top_k ({resolved_top_k})."
            )

        # 4. document_id validation
        resolved_doc_id: str | None = None
        if document_id is not None:
            if not isinstance(document_id, str) or not document_id.strip():
                raise HybridQueryValidationError(
                    "document_id filter must be a non-empty string if provided."
                )
            resolved_doc_id = document_id.strip()

        start_total = time.perf_counter()

        # 5. Dense query embedding
        start_dense = time.perf_counter()
        try:
            dense_vectors = self.dense_embedder.embed_texts([clean_query])
        except DenseEmbeddingError as exc:
            logger.exception(
                "Dense query embedding failed during hybrid retrieval",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise HybridRetrievalError(
                f"Failed to generate dense query embedding: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during dense query embedding",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise HybridRetrievalError(
                f"Unexpected error during dense query embedding: {exc}"
            ) from exc
        dense_duration_ms = (time.perf_counter() - start_dense) * 1000

        if not dense_vectors or len(dense_vectors) != 1:
            got_count = len(dense_vectors) if dense_vectors else 0
            raise HybridRetrievalError(
                f"Expected exactly 1 dense query vector from embedder, got {got_count}."
            )
        dense_vector = dense_vectors[0]

        # 6. Sparse BM25 query embedding
        start_sparse = time.perf_counter()
        try:
            sparse_vector = self.sparse_embedder.embed_query(clean_query)
        except SparseEmbeddingError as exc:
            logger.exception(
                "Sparse query embedding failed during hybrid retrieval",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise HybridRetrievalError(
                f"Failed to generate sparse query embedding: {exc}"
            ) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during sparse query embedding",
                query_length=len(clean_query),
                error=str(exc),
            )
            raise HybridRetrievalError(
                f"Unexpected error during sparse query embedding: {exc}"
            ) from exc
        sparse_duration_ms = (time.perf_counter() - start_sparse) * 1000

        if not sparse_vector.indices:
            logger.info(
                "Sparse query produced no active lexical tokens; "
                "proceeding with hybrid search where sparse prefetch "
                "yields no candidates",
                query_length=len(clean_query),
            )

        # 7. Qdrant hybrid search with server-side RRF fusion
        start_search = time.perf_counter()
        try:
            results = await self.vector_store.search_hybrid(
                dense_query_vector=dense_vector,
                sparse_query_vector=sparse_vector,
                limit=resolved_top_k,
                candidate_limit=resolved_candidate_k,
                document_id=resolved_doc_id,
            )
        except QdrantVectorStoreError as exc:
            logger.exception(
                "Qdrant hybrid search failed",
                top_k=resolved_top_k,
                candidate_k=resolved_candidate_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise HybridRetrievalError(f"Hybrid vector search failed: {exc}") from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error during hybrid search",
                top_k=resolved_top_k,
                candidate_k=resolved_candidate_k,
                document_id=resolved_doc_id,
                error=str(exc),
            )
            raise HybridRetrievalError(
                f"Unexpected error during hybrid search: {exc}"
            ) from exc
        search_duration_ms = (time.perf_counter() - start_search) * 1000
        total_duration_ms = (time.perf_counter() - start_total) * 1000

        logger.info(
            "Hybrid retrieval completed",
            query_length=len(clean_query),
            candidate_k=resolved_candidate_k,
            top_k=resolved_top_k,
            has_document_filter=resolved_doc_id is not None,
            result_count=len(results),
            dense_embed_latency_ms=round(dense_duration_ms, 2),
            sparse_embed_latency_ms=round(sparse_duration_ms, 2),
            search_latency_ms=round(search_duration_ms, 2),
            total_latency_ms=round(total_duration_ms, 2),
        )

        return results

    async def search_and_rerank(
        self,
        query: str,
        top_k: int | None = None,
        candidate_k: int | None = None,
        document_id: str | None = None,
        reranker: Any = None,
    ) -> list[Any]:
        """
        Retrieve candidate chunks using hybrid retrieval (Dense + BM25 RRF)
        and rerank them using the CrossEncoderReranker.

        Args:
            query: User search query string.
            top_k: Optional final number of reranked results to return.
            candidate_k: Optional candidate limit fetched from hybrid retrieval.
            document_id: Optional document ID to filter chunks server-side.
            reranker: Optional custom CrossEncoderReranker instance.

        Returns:
            Ordered list of RerankedSearchResult items sorted by rerank_score
            descending.
        """
        from src.retrieval.reranked_retriever import RerankedHybridRetriever
        from src.retrieval.reranker import CrossEncoderReranker

        active_reranker = reranker or CrossEncoderReranker()
        orchestrator = RerankedHybridRetriever(
            hybrid_retriever=self,
            reranker=active_reranker,
        )
        return await orchestrator.search(
            query=query,
            top_k=top_k,
            candidate_k=candidate_k,
            document_id=document_id,
        )
