from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.query_route import QueryRoute


class MedicalRagAnswerRequest(BaseModel):
    """Request schema for internal medical RAG grounded answer generation."""

    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., alias="patientId", min_length=1, max_length=128)
    query: str = Field(..., min_length=1, max_length=1000)
    limit: int = Field(default=8, ge=1, le=20)
    document_type: str | None = Field(default=None, alias="documentType")
    from_date: str | datetime | None = Field(default=None, alias="fromDate")
    to_date: str | datetime | None = Field(default=None, alias="toDate")


class MedicalAnswerCitation(BaseModel):
    """Attributed citation metadata programmatically mapped from retrieved chunks."""

    model_config = ConfigDict(populate_by_name=True)

    citation_id: int = Field(..., alias="citationId", serialization_alias="citationId")
    document_id: str = Field(..., alias="documentId", serialization_alias="documentId")
    version_id: str | None = Field(default=None, alias="versionId", serialization_alias="versionId")
    chunk_id: str | None = Field(default=None, alias="chunkId", serialization_alias="chunkId")
    file_name: str = Field(..., alias="fileName", serialization_alias="fileName")
    document_type: str = Field(..., alias="documentType", serialization_alias="documentType")
    report_date: str | None = Field(default=None, alias="reportDate", serialization_alias="reportDate")
    page_number: int | None = Field(default=None, alias="pageNumber", serialization_alias="pageNumber")
    chunk_index: int | None = Field(default=None, alias="chunkIndex", serialization_alias="chunkIndex")
    content: str | None = Field(default=None, alias="content", serialization_alias="content")
    score: float | None = Field(default=None, alias="score", serialization_alias="score")
    source_type: str = Field(
        default="DOCUMENT_CHUNK", alias="sourceType", serialization_alias="sourceType"
    )
    observation_id: str | None = Field(
        default=None, alias="observationId", serialization_alias="observationId"
    )


class MedicalRagAnswerResponse(BaseModel):
    """Response schema containing grounded answer and verified citation attributions."""

    model_config = ConfigDict(populate_by_name=True)

    answer: str
    route: QueryRoute = QueryRoute.RAG
    requires_history: bool = Field(default=False, alias="requiresHistory", serialization_alias="requiresHistory")
    citations: list[MedicalAnswerCitation] = Field(default_factory=list)
    result_count: int = Field(default=0, alias="resultCount", serialization_alias="resultCount")


class MedicalChatMessage(BaseModel):
    """Individual message in doctor medical chat history."""

    model_config = ConfigDict(populate_by_name=True)

    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., min_length=1, description="Message text content")


class MedicalChatRequest(BaseModel):
    """Request schema for internal medical multi-turn chat generation."""

    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., alias="patientId", min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, alias="conversationId", min_length=1, max_length=255)
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[MedicalChatMessage] = Field(default_factory=list)
    limit: int = Field(default=8, ge=1, le=20)
    document_type: str | None = Field(default=None, alias="documentType")
    from_date: str | datetime | None = Field(default=None, alias="fromDate")
    to_date: str | datetime | None = Field(default=None, alias="toDate")


class MedicalChatResponse(BaseModel):
    """Response schema for non-streaming medical chat answer."""

    model_config = ConfigDict(populate_by_name=True)

    route: QueryRoute = QueryRoute.RAG
    answer: str
    citations: list[MedicalAnswerCitation] = Field(default_factory=list)
    result_count: int = Field(default=0, alias="resultCount", serialization_alias="resultCount")
    retrieval_query: str | None = Field(
        default=None, alias="retrievalQuery", serialization_alias="retrievalQuery"
    )
    rewritten: bool = False
    requires_history: bool = Field(default=False, alias="requiresHistory", serialization_alias="requiresHistory")
    context_message_ids: list[str] = Field(default_factory=list, alias="contextMessageIds", serialization_alias="contextMessageIds")
    message_id: str | None = Field(default=None, alias="messageId", serialization_alias="messageId")
    answer_mode: str | None = Field(
        default=None, alias="answerMode", serialization_alias="answerMode"
    )

