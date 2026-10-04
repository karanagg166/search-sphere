from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


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
    file_name: str = Field(..., alias="fileName", serialization_alias="fileName")
    document_type: str = Field(..., alias="documentType", serialization_alias="documentType")
    report_date: str | None = Field(default=None, alias="reportDate", serialization_alias="reportDate")
    page_number: int | None = Field(default=None, alias="pageNumber", serialization_alias="pageNumber")
    chunk_index: int | None = Field(default=None, alias="chunkIndex", serialization_alias="chunkIndex")
    content: str | None = Field(default=None, alias="content", serialization_alias="content")
    score: float | None = Field(default=None, alias="score", serialization_alias="score")


class MedicalRagAnswerResponse(BaseModel):
    """Response schema containing grounded answer and verified citation attributions."""

    model_config = ConfigDict(populate_by_name=True)

    answer: str
    citations: list[MedicalAnswerCitation] = Field(default_factory=list)
    result_count: int = Field(default=0, alias="resultCount", serialization_alias="resultCount")
