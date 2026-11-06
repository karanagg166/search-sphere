from datetime import UTC, datetime, timedelta

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.external_document import ExternalDocument
from src.models.ingestion_job import IngestionJob
from src.repositories.external_registration import register_external_document
from src.repositories.ingestion_queue import ingestion_queue
from src.routers.internal_medical_documents import (
    ALLOWED_MIME_TYPES,
    MAX_MEDICAL_DOC_SIZE_BYTES,
    sanitize_filename,
    sanitize_identifier,
)
from src.schemas.internal_medical_documents import (
    MedicalDocumentDeleteIndexResponse,
    MedicalDocumentIngestRequest,
    MedicalDocumentIngestResponse,
    MedicalDocumentStaleRecoveryResponse,
    MedicalDocumentStatusResponse,
)
from src.security.medical_context import (
    bind_patient,
    get_medical_context,
    validate_storage_scope,
)
from src.security.service_context import ServiceContext
from src.services.ingestion_progress import attach_job_progress
from src.vector_store import get_vector_store

# Vector store provider; defaults to get_vector_store factory,
# exposed as QdrantVectorStore for backward compatibility with existing tests.
QdrantVectorStore = get_vector_store

logger = structlog.get_logger()

router = APIRouter(prefix="/internal/medical-documents", tags=["Internal Medical Documents Ingestion"])


def parse_report_date(date_val: datetime | str | None) -> datetime | None:
    if not date_val:
        return None
    if isinstance(date_val, datetime):
        return date_val if date_val.tzinfo else date_val.replace(tzinfo=UTC)
    try:
        clean_str = date_val.strip()
        if clean_str.endswith("Z"):
            clean_str = clean_str[:-1] + "+00:00"
        parsed = datetime.fromisoformat(clean_str)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except Exception:
        return None


@router.post(
    "/stale/recover",
    response_model=MedicalDocumentStaleRecoveryResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Identify and recover or fail stale ingestion jobs stuck in PROCESSING",
)
async def recover_stale_documents(
    request: Request,
    threshold_minutes: int = Query(15, ge=1, le=1440, alias="thresholdMinutes"),
    action: str = Query("fail", pattern="^(fail|requeue)$"),
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentStaleRecoveryResponse:
    cutoff = datetime.now(UTC) - timedelta(minutes=threshold_minutes)
    stmt = select(ExternalDocument).where(
        ExternalDocument.source_system == "quick_clinic",
        ExternalDocument.tenant_id == _auth.tenant_id,
        ExternalDocument.status == "PROCESSING",
        ExternalDocument.updated_at < cutoff,
    )
    if _auth.subject_id:
        stmt = stmt.where(ExternalDocument.owner_subject_id == _auth.subject_id)
    if _auth.collection_id:
        stmt = stmt.where(ExternalDocument.collection_id == _auth.collection_id)
    result = await session.execute(stmt)
    stale_docs = list(result.scalars().all())
    recovered_ids: list[str] = []

    for doc in stale_docs:
        job = (await session.execute(select(IngestionJob).where(
            IngestionJob.document_kind == "external", IngestionJob.document_id == doc.id,
        ).with_for_update())).scalar_one_or_none()
        if job and job.status == "RUNNING" and job.lease_expires_at:
            expiry = job.lease_expires_at if job.lease_expires_at.tzinfo else job.lease_expires_at.replace(tzinfo=UTC)
            if expiry > datetime.now(UTC):
                continue
        recovered_ids.append(doc.external_document_id)
        if action == "fail":
            job = job or await ingestion_queue.enqueue(session, "external", doc.id)
            job.status, job.stage, job.error_code = "FAILED", "failed", "lease_expired"
            job.lease_token = job.lease_expires_at = None
            await ingestion_queue.mirror_document_state(session, job)
        elif action == "requeue":
            doc.status = "QUEUED"
            doc.processing_error = None
            await ingestion_queue.enqueue(session, "external", doc.id, reset=True)

    await session.commit()
    from src.services.ingestion_worker import ingestion_worker

    ingestion_worker.notify_new_job()

    logger.info(
        "Stale medical processing recovery executed",
        stale_count=len(recovered_ids),
        action=action,
        threshold_minutes=threshold_minutes,
    )

    return MedicalDocumentStaleRecoveryResponse(
        stale_count=len(recovered_ids),
        recovered_document_ids=recovered_ids,
        action=action,
        threshold_minutes=threshold_minutes,
    )


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
    request: Request,
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentIngestResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    _auth = bind_patient(_auth, clean_patient_id)

    storage_path = body.storage_path.strip()
    validate_storage_scope(_auth, storage_path)
    if storage_path.split("/")[2] != clean_document_id:
        raise HTTPException(status_code=403, detail="Storage document identity mismatch.")
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
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {MAX_MEDICAL_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
        )

    clean_file_name = sanitize_filename(body.file_name)
    clean_doc_type = body.document_type.strip().upper()
    parsed_report_date = parse_report_date(body.report_date)

    record, _ = await register_external_document(session, {
        "source_system": "quick_clinic", "tenant_id": _auth.tenant_id,
        "collection_id": _auth.collection_id, "owner_subject_id": clean_patient_id,
        "external_document_id": clean_document_id, "external_patient_id": clean_patient_id,
        "storage_path": storage_path, "file_name": clean_file_name, "mime_type": mime_type,
        "file_size": body.file_size, "document_type": clean_doc_type, "report_date": parsed_report_date,
    }, reindex=body.reindex)
    await session.commit()
    await session.refresh(record)
    from src.services.ingestion_worker import ingestion_worker

    ingestion_worker.notify_new_job()

    await attach_job_progress(session, "external", [record])
    response.status_code = status.HTTP_202_ACCEPTED
    return MedicalDocumentIngestResponse(
        documentId=clean_document_id,
        status=record.status,
        job=record.job,
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
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentStatusResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")

    result = await session.execute(
        select(ExternalDocument).where(
            ExternalDocument.source_system == "quick_clinic",
            ExternalDocument.tenant_id == _auth.tenant_id,
            ExternalDocument.external_document_id == clean_document_id,
        )
    )
    record = result.scalar_one_or_none()

    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Medical document '{clean_document_id}' not found.",
        )

    bind_patient(_auth, record.external_patient_id)
    await attach_job_progress(session, "external", [record])
    return MedicalDocumentStatusResponse(
        documentId=record.external_document_id,
        status=record.status,
        job=record.job,
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
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
) -> MedicalDocumentDeleteIndexResponse:
    clean_document_id = sanitize_identifier(document_id, "document_id")

    result = await session.execute(select(ExternalDocument).where(
        ExternalDocument.source_system == _auth.client_id,
        ExternalDocument.tenant_id == _auth.tenant_id,
        ExternalDocument.external_document_id == clean_document_id,
    ))
    record = result.scalar_one_or_none()
    if not record:
        from src.repositories.document_deletion import find_deletion

        prior = await find_deletion(session, "external", _auth.client_id, _auth.tenant_id, clean_document_id, context=_auth)
        if prior:
            return MedicalDocumentDeleteIndexResponse(success=True, message="Document deletion already accepted.")
        raise HTTPException(status_code=404, detail="Medical document not found.")
    scope = bind_patient(_auth, record.external_patient_id)
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.services.cleanup_worker import DocumentCleanupWorker
    from src.services.document_deletion import delete_registered_document
    from src.storage.object_storage import get_object_storage

    deletion = await delete_registered_document(
        session, "external", clean_document_id, scope.client_id, scope.tenant_id, context=scope,
    )
    await session.commit()
    await DocumentCleanupWorker(async_sessionmaker(session.bind, expire_on_commit=False), get_object_storage).run_once(deletion.id)

    return MedicalDocumentDeleteIndexResponse(
        success=True,
        message="Document access revoked. Poll cleanup to verify physical deletion.",
    )


@router.get("/{document_id}/cleanup", summary="Verify authorized medical document physical cleanup")
async def get_medical_document_cleanup(
    document_id: str,
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
) -> dict:
    from src.repositories.document_deletion import cleanup_receipt, find_deletion

    row = await find_deletion(session, "external", _auth.client_id, _auth.tenant_id, document_id, context=_auth)
    return await cleanup_receipt(session, row)
