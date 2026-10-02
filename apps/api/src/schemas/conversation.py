from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.config import settings
from src.schemas.answer import AnswerSource


class ConversationCreate(BaseModel):
    """Payload to create a new conversation."""

    title: str | None = Field(
        default=None,
        description="Optional title for the conversation. Defaults to 'New Conversation'.",
        max_length=255,
    )

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            return v_clean if v_clean else None
        return None


class ConversationUpdate(BaseModel):
    """Payload to update an existing conversation title."""

    title: str = Field(
        ...,
        description="Updated title for the conversation.",
        min_length=1,
        max_length=255,
    )

    @field_validator("title")
    @classmethod
    def clean_title(cls, v: str) -> str:
        v_clean = v.strip()
        if not v_clean:
            raise ValueError("Title must not be empty or whitespace.")
        return v_clean


class MessageResponse(BaseModel):
    """Representation of a persisted conversation message."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    original_query: str | None = None
    retrieval_query: str | None = None
    rewritten: bool = False
    sources: list[AnswerSource] | None = None
    created_at: datetime


class ConversationSummaryResponse(BaseModel):
    """Summary of a conversation for list views."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationResponse(BaseModel):
    """Detailed view of a conversation with full message history."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[MessageResponse] = []


class ConversationAnswerRequest(BaseModel):
    """Request payload to ask a question within an ongoing conversation."""

    query: str = Field(
        ...,
        description="Question or search query.",
        min_length=1,
    )
    top_k: int = Field(
        default_factory=lambda: settings.RERANKER_TOP_K,
        description="Number of final reranked search results to supply as context.",
        gt=0,
    )
    candidate_k: int = Field(
        default_factory=lambda: settings.HYBRID_SEARCH_CANDIDATE_K,
        description="Number of candidates retrieved before cross-encoder reranking.",
        gt=0,
    )
    document_id: str | None = Field(
        default=None,
        description="Optional document ID to scope retrieval to a single document.",
    )

    @field_validator("query")
    @classmethod
    def validate_query(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Query must be a non-empty, non-whitespace string.")
        cleaned = v.strip()
        if len(cleaned) > settings.MAX_QUERY_LENGTH:
            raise ValueError(
                f"Query exceeds maximum allowed limit of {settings.MAX_QUERY_LENGTH} characters."
            )
        return cleaned

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
                raise ValueError("document_id filter must be a non-empty string if provided.")
            return v.strip()
        return None

    @model_validator(mode="after")
    def validate_candidate_k_ge_top_k(self) -> "ConversationAnswerRequest":
        if self.candidate_k < self.top_k:
            raise ValueError(
                f"candidate_k ({self.candidate_k}) cannot be less than top_k ({self.top_k})."
            )
        return self


class ConversationAnswerResponse(BaseModel):
    """Response returned when an answer is generated within a conversation."""

    conversation_id: str
    message: MessageResponse
    query: str
    retrieval_query: str
    rewritten: bool
    answer: str
    sources: list[AnswerSource] = []
