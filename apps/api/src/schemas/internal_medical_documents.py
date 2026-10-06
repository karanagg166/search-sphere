from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MedicalDocumentIngestRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., alias="patientId")
    storage_path: str = Field(..., alias="storagePath")
    file_name: str = Field(..., alias="fileName")
    mime_type: str = Field(..., alias="mimeType")
    file_size: int = Field(..., alias="fileSize")
    document_type: str = Field(..., alias="documentType")
    report_date: datetime | str | None = Field(None, alias="reportDate")


class MedicalDocumentIngestResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(..., alias="documentId", serialization_alias="documentId")
    status: str


class MedicalDocumentStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    document_id: str = Field(..., alias="documentId", serialization_alias="documentId")
    status: str
    processed_at: datetime | str | None = Field(None, alias="processedAt", serialization_alias="processedAt")
    error: str | None = None


class MedicalDocumentDeleteIndexResponse(BaseModel):
    success: bool
    message: str


class MedicalDocumentStaleRecoveryResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stale_count: int = Field(..., alias="staleCount", serialization_alias="staleCount")
    recovered_document_ids: list[str] = Field(
        default_factory=list,
        alias="recoveredDocumentIds",
        serialization_alias="recoveredDocumentIds",
    )
    action: str
    threshold_minutes: int = Field(
        ..., alias="thresholdMinutes", serialization_alias="thresholdMinutes"
    )
