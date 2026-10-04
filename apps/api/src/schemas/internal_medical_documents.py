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
