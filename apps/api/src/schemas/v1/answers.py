from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.query_route import QueryRoute


class ConversationMessageSchema(BaseModel):
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str = Field(..., min_length=1)


class AnswerCitation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    citation_number: int
    chunk_id: str
    document_id: str
    file_name: str | None = None
    page_number: int | None = None
    text_snippet: str
    collection_id: str | None = None
    owner_subject_id: str | None = None


class AnswerRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(..., min_length=1, max_length=2000, description="User question to answer")
    conversation_id: str | None = Field(default=None, min_length=1, max_length=36)
    collection_id: str | None = Field(None, max_length=128, description="Target collection scope")
    owner_subject_id: str | None = Field(None, max_length=255, description="Target owner/subject scope")
    limit: int = Field(default=5, ge=1, le=20, description="Number of context chunks to retrieve")
    document_type: str | None = Field(None, max_length=64)
    system_prompt: str | None = Field(
        None,
        max_length=2000,
        description="Deprecated compatibility field; server-owned grounding policy always applies.",
    )
    conversation_history: list[ConversationMessageSchema] | None = Field(
        None,
        description="Deprecated compatibility field; authorized history is loaded using conversation_id.",
    )
    metadata_filters: dict[str, Any] | None = Field(
        None,
        description="Optional additional metadata filters",
    )


class AnswerResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    answer: str
    citations: list[AnswerCitation]
    retrieved_chunk_count: int
    duration_ms: float
    route: QueryRoute = QueryRoute.RAG
    requires_history: bool = False
    context_message_ids: list[str] = Field(default_factory=list)
    retrieval_query: str | None = None
    rewritten: bool = False
    message_id: str | None = None
