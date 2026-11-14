"""Shared data contracts, exceptions, and retrieval ports for search-sphere-rag."""

from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class SearchSphereRagError(Exception):
    """Base exception for search-sphere-rag."""


class EmbeddingValidationError(SearchSphereRagError):
    """Raised when embedding vectors or provider outputs fail validation."""


class EmbeddingSpaceMismatchError(SearchSphereRagError):
    """Raised when combining or querying incompatible embedding spaces."""


class RetrievalValidationError(SearchSphereRagError):
    """Raised when retrieval query or parameters fail validation."""


class ProviderCallError(SearchSphereRagError):
    """Raised when a hosted inference API call fails or times out."""


class EmbeddingMode(StrEnum):
    DOCUMENT = "document"
    QUERY = "query"


class QueryIntentRoute(StrEnum):
    DIRECT = "DIRECT"
    RAG = "RAG"
    CLARIFICATION = "CLARIFICATION"
    TOOL_API = "TOOL_API"
    GENERAL_LLM = "GENERAL_LLM"


class EmbeddingSpace(BaseModel):
    """Immutable descriptor of a vector embedding space."""

    model_config = ConfigDict(frozen=True)

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    version: str = Field(default="v1", min_length=1)
    dimension: int = Field(gt=0)
    normalized: bool = True

    def assert_compatible(self, other: "EmbeddingSpace") -> None:
        if (
            self.provider != other.provider
            or self.model != other.model
            or self.version != other.version
            or self.dimension != other.dimension
        ):
            raise EmbeddingSpaceMismatchError(
                f"Incompatible embedding spaces: "
                f"{self.provider}/{self.model}@{self.version}({self.dimension}d) != "
                f"{other.provider}/{other.model}@{other.version}({other.dimension}d)"
            )


class EmbeddingResult(BaseModel):
    """Validated batch embedding output with input provenance hashes."""

    model_config = ConfigDict(frozen=True)

    space: EmbeddingSpace
    mode: EmbeddingMode
    vectors: list[list[float]]
    input_hashes: list[str]


class ProvenanceRef(BaseModel):
    """Source coordinate / object reference for an evidence chunk."""

    model_config = ConfigDict(extra="allow")

    page_number: int | None = None
    source_object_id: str | None = None
    kind: str | None = None


class RetrievalCandidate(BaseModel):
    """Candidate chunk returned by a dense or sparse storage adapter."""

    model_config = ConfigDict(extra="allow")

    chunk_id: str = Field(min_length=1)
    chunk_stable_id: str | None = None
    document_id: str = Field(min_length=1)
    document_version_id: str | None = None
    document_title: str = ""
    document_type: str | None = None
    content: str
    content_sha256: str | None = None
    kind: str = "text"
    start_page: int = 1
    end_page: int = 1
    page_numbers: list[int] = Field(default_factory=list)
    source_object_ids: list[str] = Field(default_factory=list)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    score: float = 0.0
    rank: int | None = None
    review_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FusedCandidate(BaseModel):
    """Candidate after dense + sparse Reciprocal Rank Fusion."""

    candidate: RetrievalCandidate
    dense_score: float | None = None
    sparse_score: float | None = None
    rrf_score: float
    dense_rank: int | None = None
    sparse_rank: int | None = None
    final_rank: int = 1


class RerankedResult(BaseModel):
    """Final ranked evidence unit after optional hosted reranking."""

    candidate: RetrievalCandidate
    dense_score: float | None = None
    sparse_score: float | None = None
    hybrid_score: float
    rerank_score: float
    final_rank: int
    reranked_by: str = "hybrid_fallback"


class RetrievalResponse(BaseModel):
    """Complete retrieval trace returned by HybridRagEngine."""

    query: str
    strategy: str
    embedding_space: EmbeddingSpace | None = None
    candidate_count: int
    reranked: bool
    reranker_model: str | None = None
    duration_ms: int
    results: list[RerankedResult]


class ConversationTurn(BaseModel):
    role: str
    content: str
    message_id: str | None = None


class QueryRewriteResult(BaseModel):
    original_query: str
    rewritten_query: str
    was_rewritten: bool
    requires_history: bool
    reason: str = ""


class QueryIntentResult(BaseModel):
    route: QueryIntentRoute
    confidence: float = Field(ge=0.0, le=1.0)
    requires_history: bool = False
    reason: str = ""


@runtime_checkable
class VectorRetrieverPort(Protocol):
    async def search_dense(
        self,
        query_vector: Sequence[float],
        *,
        space: EmbeddingSpace,
        scope: Mapping[str, Any],
        limit: int,
    ) -> list[RetrievalCandidate]: ...


@runtime_checkable
class LexicalRetrieverPort(Protocol):
    async def search_lexical(
        self,
        query: str,
        *,
        scope: Mapping[str, Any],
        limit: int,
    ) -> list[RetrievalCandidate]: ...
