from datetime import datetime

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.external_document import ExternalDocument
from src.routers.internal_medical_documents import (
    ALLOWED_MIME_TYPES,
    MAX_MEDICAL_DOC_SIZE_BYTES,
    sanitize_filename,
    sanitize_identifier,
    verify_service_secret,
)
from src.schemas.internal_medical_documents import (
    MedicalDocumentDeleteIndexResponse,
    MedicalDocumentIngestRequest,
    MedicalDocumentIngestResponse,
    MedicalDocumentStatusResponse,
)
from src.tasks.document_tasks import enqueue_medical_document
from src.vector_store import QdrantVectorStore

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-documents", tags=["Internal Medical Documents Ingestion"])


def parse_report_date(date_val: datetime | str | None) -> datetime | None:
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val
    try:
        clean_str = date_val.strip()
        if clean_str.endswith("Z"):
            clean_str = clean_str[:-1] + "+00:00"
        return datetime.fromisoformat(clean_str)
    except Exception:
        return None


@router.post(
    "/{document_id}/ingest",
    response_model=MedicalDocumentIngestResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue medical document for asynchronous indexing and vector extraction",
    description="Registers an external medical document, creates or updates the ingestion record, and enqueues a background job.",
)
async def ingest_medical_document(
    document_id: str,
    body: MedicalDocumentIngestRequest,
    response: Response,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentIngestResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")

    storage_path = body.storage_path.strip()
    if not storage_path.startswith("medical-documents/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid storage path. Must begin with 'medical-documents/'.",
        )
    if ".." in storage_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Path traversal is not permitted in storage path.",
        )

    mime_type = body.mime_type.strip().lower()
    if mime_type == "image/jpg":
        mime_type = "image/jpeg"
    if mime_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported mime type '{mime_type}'. Only PDF, JPEG, PNG, and WebP are supported.",
        )

    if body.file_size <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="file_size must be a positive integer.",
        )
    if body.file_size > MAX_MEDICAL_DOC_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_MEDICAL_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    clean_file_name = sanitize_filename(body.file_name)
    clean_doc_type = body.document_type.strip().upper()
    parsed_report_date = parse_report_date(body.report_date)

    # Idempotent upsert check on (source_system, external_document_id)
    result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_document_id == clean_document_id,
        )
    )
    existing_record = result.scalar_one_or_none()

    if existing_record:
        existing_record.external_patient_id = clean_patient_id
        existing_record.storage_path = storage_path
        existing_record.file_name = clean_file_name
        existing_record.mime_type = mime_type
        existing_record.file_size = body.file_size
        existing_record.document_type = clean_doc_type
        existing_record.report_date = parsed_report_date
        existing_record.status = "QUEUED"
        existing_record.processing_error = None
        logger.info(
            "Reusing existing external document record for re-ingestion",
            document_id=clean_document_id,
            patient_id=clean_patient_id,
        )
    else:
        new_record = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=clean_document_id,
            external_patient_id=clean_patient_id,
            storage_path=storage_path,
            file_name=clean_file_name,
            mime_type=mime_type,
            file_size=body.file_size,
            document_type=clean_doc_type,
            report_date=parsed_report_date,
            status="QUEUED",
        )
        session.add(new_record)
        logger.info(
            "Created new external document record for ingestion",
            document_id=clean_document_id,
            patient_id=clean_patient_id,
        )

    await session.commit()

    # Enqueue background processing task via RabbitMQ/Dramatiq
    enqueued = enqueue_medical_document(clean_document_id)
    if not enqueued:
        logger.warning(
            "Medical document queued in database but worker queue unavailable",
            document_id=clean_document_id,
        )

    response.status_code = status.HTTP_202_ACCEPTED
    return MedicalDocumentIngestResponse(
        documentId=clean_document_id,
        status="QUEUED",
    )


@router.get(
    "/{document_id}/status",
    response_model=MedicalDocumentStatusResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Get processing status of an ingested medical document",
)
async def get_medical_document_status(
    document_id: str,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentStatusResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")

    result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_document_id == clean_document_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medical document '{clean_document_id}' not found.",
        )

    return MedicalDocumentStatusResponse(
        documentId=record.external_document_id,
        status=record.status,
        processedAt=record.processed_at,
        error=record.processing_error,
    )


@router.delete(
    "/{document_id}/index",
    response_model=MedicalDocumentDeleteIndexResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete vector embeddings, extracted text, and ingestion record for a medical document",
)
async def delete_medical_document_index(
    document_id: str,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentDeleteIndexResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")

    # 1. Clean vector store points from Qdrant
    vector_store = QdrantVectorStore()
    try:
        await vector_store.delete_document_points(clean_document_id)
    except Exception as exc:
        logger.warning(
            "Vector points deletion encountered an error (or points do not exist)",
            document_id=clean_document_id,
            error=str(exc),
        )
    finally:
        await vector_store.close()

    # 2. Delete database ingestion metadata and cascaded content
    result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.external_document_id == clean_document_id,
        )
    )
    record = result.scalar_one_or_none()
    if record:
        await session.delete(record)
        await session.commit()
        logger.info(
            "External document record and extracted text deleted",
            document_id=clean_document_id,
        )

    return MedicalDocumentDeleteIndexResponse(
        success=True,
        message="Medical document index and content cleaned up successfully",
    )
