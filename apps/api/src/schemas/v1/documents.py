from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentUploadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    storage_key: str = Field(..., description="Object storage path for the uploaded file")
    mime_type: str = Field(..., description="Validated MIME type of the file")
    file_size: int = Field(..., description="Size of file in bytes")


class DocumentRegisterRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    external_document_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Caller's unique identifier for this document",
    )
    storage_key: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Object storage path returned from upload",
    )
    file_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Original name of the document file",
    )
    mime_type: str = Field(
        ...,
        min_length=3,
        max_length=100,
        description="MIME type of document (e.g. application/pdf)",
    )
    file_size: int = Field(
        ...,
        gt=0,
        description="Size of file in bytes",
    )
    collection_id: str | None = Field(
        None,
        max_length=128,
        description="Optional collection/knowledge base identifier",
    )
    owner_subject_id: str | None = Field(
        None,
        max_length=255,
        description="Optional owner/subject identifier (e.g. user_id, patient_id, student_id)",
    )
    document_type: str = Field(
        default="GENERAL",
        max_length=64,
        description="Logical document category or type",
    )
    metadata: dict[str, Any] | None = Field(
        None,
        description="Arbitrary client metadata to associate with this document",
    )


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    client_id: str
    tenant_id: str
    collection_id: str | None = None
    owner_subject_id: str | None = None
    external_document_id: str
    file_name: str
    mime_type: str
    file_size: int
    document_type: str
    storage_path: str
    status: str
    processing_error: str | None = None
    metadata_json: dict[str, Any] | None = Field(None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]


class DocumentDeleteResponse(BaseModel):
    success: bool
    message: str
    document_id: str


class SignedUrlResponse(BaseModel):
    url: str
    expires_in: int
