from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class DocumentCollection:
    id: str
    client_id: str
    tenant_id: str
    collection_id: str
    name: str
    description: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class DocumentRecord:
    id: str
    client_id: str
    tenant_id: str
    external_document_id: str
    file_name: str
    mime_type: str
    file_size: int
    document_type: str
    storage_path: str
    status: str
    collection_id: str | None = None
    owner_subject_id: str | None = None
    processing_error: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class SearchChunkResult:
    chunk_id: str
    document_id: str
    text: str
    score: float
    rank: int
    start_page: int | None = None
    end_page: int | None = None
    block_types: list[str] = field(default_factory=list)
    client_id: str | None = None
    tenant_id: str | None = None
    collection_id: str | None = None
    owner_subject_id: str | None = None
    document_type: str | None = None
    file_name: str | None = None


@dataclass
class SearchResponse:
    query: str
    total: int
    results: list[SearchChunkResult]
    duration_ms: float


@dataclass
class AnswerCitation:
    citation_number: int
    chunk_id: str
    document_id: str
    file_name: str | None
    page_number: int | None
    text_snippet: str
    collection_id: str | None = None
    owner_subject_id: str | None = None


@dataclass
class AnswerResponse:
    answer: str
    citations: list[AnswerCitation]
    retrieved_chunk_count: int
    duration_ms: float
