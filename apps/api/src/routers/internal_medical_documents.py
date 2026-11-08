import os
import re

import structlog
from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.security.medical_context import (
    bind_patient,
    get_medical_context,
    validate_storage_scope,
)
from src.security.service_auth import verify_service_secret  # noqa: F401
from src.security.service_context import ServiceContext
from src.storage.object_storage import ObjectStorage, get_object_storage

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-documents", tags=["Internal Medical Documents Storage"])

MAX_MEDICAL_DOC_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

ALLOWED_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
}


def detect_and_validate_file_type(data: bytes, reported_content_type: str | None) -> str:
    """Validates magic bytes against allowed medical document MIME types."""
    detected: str | None = None

    if data.startswith(b"%PDF"):
        detected = "application/pdf"
    elif data.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        detected = "image/webp"

    if not detected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Only PDF, JPEG, PNG, and WebP files are allowed.",
        )

    if reported_content_type == "image/jpg":
        reported_content_type = "image/jpeg"

    if reported_content_type and reported_content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported reported content type '{reported_content_type}'.",
        )

    from src.processing.file_validation import validate_binary_document
    validate_binary_document(data, detected, reported_content_type)
    return detected


def sanitize_filename(filename: str) -> str:
    """Sanitizes file name to prevent directory traversal and illegal characters."""
    base_name = os.path.basename(filename).strip()
    if not base_name:
        base_name = "document"

    name_part, ext = os.path.splitext(base_name)
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name_part).strip("._")
    if not clean_name:
        clean_name = "document"

    clean_ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower()
    if clean_ext:
        return f"{clean_name[:100]}.{clean_ext[:10]}"
    return clean_name[:100]


def sanitize_identifier(identifier: str, field_name: str) -> str:
    """Validates and cleans patient_id and document_id against path traversal."""
    cleaned = identifier.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required and cannot be empty.",
        )
    if not re.match(r"^[a-zA-Z0-9_\-\.:]+$", cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid characters in {field_name}.",
        )
    return cleaned


# Response Schemas
class MedicalDocumentUploadResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    storage_path: str = Field(..., alias="storagePath", serialization_alias="storagePath")
    mime_type: str = Field(..., alias="mimeType", serialization_alias="mimeType")
    file_size: int = Field(..., alias="fileSize", serialization_alias="fileSize")


class SignedUrlResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str
    expires_in: int = Field(..., alias="expiresIn", serialization_alias="expiresIn")


class DeleteResponse(BaseModel):
    success: bool
    message: str


class DeleteRequestBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    storage_path: str | None = Field(None, alias="storagePath")


@router.post(
    "",
    response_model=MedicalDocumentUploadResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
    summary="Store medical document file in private object storage",
    description="Accepts document bytes from Quick Clinic, validates size/type, and persists to private Supabase storage.",
)
async def upload_medical_document(
    file: UploadFile = File(..., description="The medical document file"),
    patient_id: str = Form(..., description="Unique patient identifier"),
    document_id: str = Form(..., description="Unique medical document identifier"),
    _auth: ServiceContext = Depends(get_medical_context),
    storage: ObjectStorage = Depends(get_object_storage),
    db: AsyncSession = Depends(get_db),
) -> MedicalDocumentUploadResponse:
    clean_patient_id = sanitize_identifier(patient_id, "patient_id")
    _auth = bind_patient(_auth, clean_patient_id)
    clean_document_id = sanitize_identifier(document_id, "document_id")

    data = await file.read(MAX_MEDICAL_DOC_SIZE_BYTES + 1)
    if len(data) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    if len(data) > MAX_MEDICAL_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_MEDICAL_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    detected_mime = detect_and_validate_file_type(data, file.content_type)
    clean_filename = sanitize_filename(file.filename or "document")

    storage_path = f"medical-documents/{clean_patient_id}/{clean_document_id}/{clean_filename}"
    validate_storage_scope(_auth, storage_path)

    from sqlalchemy import select

    from src.models.external_document import ExternalDocument
    from src.repositories.document_deletion import (
        lock_document_identity,
        reject_deleted_identity,
    )

    await lock_document_identity(db, "external", _auth.client_id, _auth.tenant_id, clean_document_id)
    await reject_deleted_identity(db, "external", _auth.client_id, _auth.tenant_id, clean_document_id)
    existing = (await db.execute(select(ExternalDocument.id).where(
        ExternalDocument.source_system == _auth.client_id, ExternalDocument.tenant_id == _auth.tenant_id,
        ExternalDocument.external_document_id == clean_document_id,
    ))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="Registered document bytes cannot be overwritten. Use a new version.")

    logger.info(
        "Persisting medical document to storage",
        document_id=clean_document_id,
        path=storage_path,
        size=len(data),
        mime=detected_mime,
    )

    try:
        await storage.upload(key=storage_path, data=data, content_type=detected_mime)
    except Exception as exc:
        logger.error(
            "Failed to upload medical document to storage",
            path=storage_path,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to store document in object storage.",
        ) from exc

    return MedicalDocumentUploadResponse(
        storagePath=storage_path,
        mimeType=detected_mime,
        fileSize=len(data),
    )


@router.get(
    "/signed-url",
    response_model=SignedUrlResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Generate temporary signed URL for viewing/downloading",
    description="Returns a short-lived URL (5-15 minutes) for private medical document access.",
)
async def get_medical_document_signed_url(
    storage_path: str = Query(..., alias="storagePath", description="The private storage path of the document"),
    expires_in: int = Query(600, alias="expiresIn", ge=300, le=900, description="Expiration time in seconds (300 to 900)"),
    _auth: ServiceContext = Depends(get_medical_context),
    storage: ObjectStorage = Depends(get_object_storage),
    db: AsyncSession = Depends(get_db),
) -> SignedUrlResponse:
    cleaned_path = storage_path.strip()
    validate_storage_scope(_auth, cleaned_path)
    if not cleaned_path.startswith("medical-documents/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage path. Must begin with 'medical-documents/'.",
        )
    if ".." in cleaned_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal is not permitted.",
        )

    from src.repositories.document_deletion import find_deletion

    if await find_deletion(db, "external", _auth.client_id, _auth.tenant_id, cleaned_path.split("/")[2], context=_auth):
        raise HTTPException(status_code=404, detail="Document was deleted.")

    try:
        signed_url = await storage.create_signed_url(key=cleaned_path, expires_in=expires_in)
    except Exception as exc:
        logger.error(
            "Failed to generate signed URL for medical document",
            path=cleaned_path,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to generate secure access URL.",
        ) from exc

    return SignedUrlResponse(
        url=signed_url,
        expiresIn=expires_in,
    )


@router.delete(
    "",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete medical document object from private storage",
    description="Revokes access and queues private medical document cleanup. Poll the cleanup receipt to verify physical deletion.",
)
async def delete_medical_document(
    storage_path: str | None = Query(None, alias="storagePath", description="Storage path as query param"),
    body: DeleteRequestBody | None = Body(None),
    _auth: ServiceContext = Depends(get_medical_context),
    storage: ObjectStorage = Depends(get_object_storage),
    db: AsyncSession = Depends(get_db),
) -> DeleteResponse:
    target_path = (storage_path or (body.storage_path if body else None) or "").strip()

    validate_storage_scope(_auth, target_path)

    if not target_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="storagePath is required to delete an object.",
        )

    if not target_path.startswith("medical-documents/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage path. Must begin with 'medical-documents/'.",
        )
    if ".." in target_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal is not permitted.",
        )

    from src.services.document_deletion import delete_medical_object
    from src.services.ingestion_worker import ingestion_worker

    await delete_medical_object(db, _auth, target_path)
    await db.commit()
    ingestion_worker.notify_new_job()
    return DeleteResponse(success=True, message="Document access revoked; poll cleanup to verify physical deletion.")
