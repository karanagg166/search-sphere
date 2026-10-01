from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import settings


class SearchRequest(BaseModel):
    """
    Incoming search request payload.

    Validates:
    - query: non-empty, non-whitespace string
    - top_k: positive integer, <= RERANKER_MAX_TOP_K
    - candidate_k: positive integer, >= top_k
    - document_id: optional non-empty string filter
    """

    query: str = Field(
        ...,
        description="Search query string.",
        min_length=1,
    )
    top_k: int = Field(
        default_factory=lambda: settings.RERANKER_TOP_K,
        description="Number of final reranked search results to return.",
        gt=0,
    )
    candidate_k: int = Field(
        default_factory=lambda: settings.HYBRID_SEARCH_CANDIDATE_K,
        description="Number of candidates retrieved during hybrid stage before reranking.",
        gt=0,
    )
    document_id: str | None = Field(
        default=None,
        description="Optional document ID to scope search to a single document.",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Query must be a non-empty, non-whitespace string.")
        return v.strip()

    @field_validator("top_k")
    @classmethod
    def validate_top_k(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"top_k must be a positive integer, got {v}.")
        if v > settings.RERANKER_MAX_TOP_K:
            raise ValueError(
                f"top_k ({v}) exceeds maximum allowed limit of {settings.RERANKER_MAX_TOP_K}."
            )
        return v

    @field_validator("candidate_k")
    @classmethod
    def validate_candidate_k(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"candidate_k must be a positive integer, got {v}.")
        return v

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, v: str | None) -> str | None:
        if v is not None:
            if not isinstance(v, str) or not v.strip():
                raise ValueError(
                    "document_id filter must be a non-empty string if provided."
                )
            return v.strip()
        return None

    @model_validator(mode="after")
    def validate_candidate_k_ge_top_k(self) -> "SearchRequest":
        if self.candidate_k < self.top_k:
            raise ValueError(
                f"candidate_k ({self.candidate_k}) cannot be less than top_k ({self.top_k})."
            )
        return self


class SearchResultResponse(BaseModel):
    """Represents a single retrieved and reranked document chunk."""

    model_config = ConfigDict(from_attributes=True)

    document_id: str
    chunk_index: int
    content: str

    start_page: int
    end_page: int
    page_numbers: list[int]
    block_types: list[str]

    rrf_score: float | None = None
    rerank_score: float
    rank: int


class SearchResponse(BaseModel):
    """Top-level semantic search API response."""

    query: str
    total: int
    results: list[SearchResultResponse]
