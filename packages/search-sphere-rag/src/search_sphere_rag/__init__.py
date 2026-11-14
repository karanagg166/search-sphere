"""Public API for search-sphere-rag."""

from .contracts import (
    ConversationTurn,
    EmbeddingMode,
    EmbeddingResult,
    EmbeddingSpace,
    EmbeddingSpaceMismatchError,
    EmbeddingValidationError,
    FusedCandidate,
    LexicalRetrieverPort,
    ProvenanceRef,
    ProviderCallError,
    QueryIntentResult,
    QueryIntentRoute,
    QueryRewriteResult,
    RerankedResult,
    RetrievalCandidate,
    RetrievalResponse,
    RetrievalValidationError,
    SearchSphereRagError,
    VectorRetrieverPort,
)
from .embeddings import (
    KNOWN_EMBEDDING_DIMENSIONS,
    CohereEmbeddingProvider,
    EmbeddingProvider,
    GoogleEmbeddingProvider,
)
from .engine import HybridRagEngine
from .fusion import deduplicate_candidates, reciprocal_rank_fusion
from .query import (
    classify_query_intent,
    preserves_query_identifiers,
    requires_history,
    rewrite_query,
)
from .reranking import CohereRerankProvider, HybridFallbackReranker, RerankerProvider
from .validation import compute_content_sha256, l2_norm, validate_vectors

__all__ = [
    "CohereEmbeddingProvider",
    "CohereRerankProvider",
    "ConversationTurn",
    "EmbeddingMode",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EmbeddingSpace",
    "EmbeddingSpaceMismatchError",
    "EmbeddingValidationError",
    "FusedCandidate",
    "GoogleEmbeddingProvider",
    "HybridFallbackReranker",
    "HybridRagEngine",
    "KNOWN_EMBEDDING_DIMENSIONS",
    "LexicalRetrieverPort",
    "ProvenanceRef",
    "ProviderCallError",
    "QueryIntentResult",
    "QueryIntentRoute",
    "QueryRewriteResult",
    "RerankedResult",
    "RerankerProvider",
    "RetrievalCandidate",
    "RetrievalResponse",
    "RetrievalValidationError",
    "SearchSphereRagError",
    "VectorRetrieverPort",
    "classify_query_intent",
    "compute_content_sha256",
    "deduplicate_candidates",
    "l2_norm",
    "preserves_query_identifiers",
    "reciprocal_rank_fusion",
    "requires_history",
    "rewrite_query",
    "validate_vectors",
]
