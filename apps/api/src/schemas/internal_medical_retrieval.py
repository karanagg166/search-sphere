from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class MedicalRetrievalSearchRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., alias="patientId", description="Quick Clinic patient identifier")
    query: str = Field(..., min_length=1, max_length=1000, description="Doctor medical record search query")
    limit: int = Field(default=8, ge=1, le=50, description="Maximum number of relevant chunks to return")
    document_type: str | None = Field(default=None, alias="documentType", description="Optional filter by document type")
    from_date: datetime | str | None = Field(default=None, alias="fromDate", description="Optional filter by report date start")
    to_date: datetime | str | None = Field(default=None, alias="toDate", description="Optional filter by report date end")


class MedicalRetrievalChunkResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    score: float = Field(..., description="Relevance score (cross-encoder rerank or hybrid fusion)")
    content: str = Field(..., description="Relevant medical document chunk text excerpt")
    document_id: str = Field(..., alias="documentId", serialization_alias="documentId")
    document_type: str | None = Field(default=None, alias="documentType", serialization_alias="documentType")
    report_date: str | None = Field(default=None, alias="reportDate", serialization_alias="reportDate")
    file_name: str | None = Field(default=None, alias="fileName", serialization_alias="fileName")
    page_number: int | None = Field(default=None, alias="pageNumber", serialization_alias="pageNumber")
    chunk_index: int = Field(..., alias="chunkIndex", serialization_alias="chunkIndex")
    patient_id: str | None = Field(default=None, alias="patientId", serialization_alias="patientId")


class MedicalRetrievalSearchResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    results: list[MedicalRetrievalChunkResult] = Field(default_factory=list)
