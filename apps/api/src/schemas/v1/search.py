from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(..., min_length=1, max_length=2000, description="Search query string")
    collection_id: str | None = Field(None, max_length=128, description="Target collection scope")
    owner_subject_id: str | None = Field(None, max_length=255, description="Target owner/subject scope")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum number of chunks to return")
    document_type: str | None = Field(None, max_length=64, description="Optional filter by document type")
    metadata_filters: dict[str, Any] | None = Field(
        None,
        description="Optional additional metadata key-value filters to match inside payload",
    )


class SearchChunkResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    chunk_id: str
    document_id: str
    text: str
    score: float
    rank: int
    start_page: int | None = None
    end_page: int | None = None
    block_types: list[str] = Field(default_factory=list)
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    document_type: str | None = None
    file_name: str | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str
    total: int
    results: list[SearchChunkResult]
    duration_ms: float
