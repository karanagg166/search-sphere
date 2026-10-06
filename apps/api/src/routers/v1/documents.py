import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.extensions.medical_cleanup import delete_observations
from src.schemas.v1.documents import (
    DocumentDeleteResponse,
    DocumentListResponse,
    DocumentRegisterRequest,
    DocumentResponse,
    DocumentUploadResponse,
    SignedUrlResponse,
)
from src.security.service_auth import get_service_context
from src.security.service_context import ServiceContext
from src.services.generic_document_service import GenericDocumentService
from src.storage.object_storage import ObjectStorage, get_object_storage
from src.vector_store.qdrant_store import QdrantVectorStore

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/documents", tags=["V1 Document Management"])


@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload document file bytes to tenant-isolated storage",
)
async def upload_document_file(
    file: UploadFile = File(..., description="Document file to upload"),
    document_id: str = Form(..., description="Unique caller identifier for this document"),
    collection_id: str | None = Form(None, description="Optional target collection identifier"),
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:write"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentUploadResponse:
    file_bytes = await file.read()
    service = GenericDocumentService(db, storage)
    storage_key, mime_type, file_size = await service.upload_file(
        context=context,
        file_bytes=file_bytes,
        original_filename=file.filename or "document",
        content_type=file.content_type,
        document_id=document_id,
        collection_id=collection_id,
    )
    return DocumentUploadResponse(
        storage_key=storage_key,
        mime_type=mime_type,
        file_size=file_size,
    )


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Register document metadata and enqueue for processing",
)
async def register_document(
    body: DocumentRegisterRequest,
    request: Request,
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:write"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    service = GenericDocumentService(db, storage)
    req_id = getattr(request.state, "request_id", None)
    doc = await service.register_document(context=context, request=body, request_id=req_id)
    return DocumentResponse.model_validate(doc)


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List documents scoped to the caller tenant",
)
async def list_documents(
    collection_id: str | None = Query(None, description="Filter by collection"),
    owner_subject_id: str | None = Query(None, description="Filter by owner/subject"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status (QUEUED, PROCESSING, READY, FAILED)"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:read"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentListResponse:
    service = GenericDocumentService(db, storage)
    docs, total = await service.list_documents(
        context=context,
        collection_id=collection_id,
        owner_subject_id=owner_subject_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
    )
    return DocumentListResponse(
        total=total,
        documents=[DocumentResponse.model_validate(d) for d in docs],
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get document details and ingestion status",
)
async def get_document(
    document_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:read"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    service = GenericDocumentService(db, storage)
    doc = await service.get_document(context, document_id)
    return DocumentResponse.model_validate(doc)


@router.get(
    "/{document_id}/signed-url",
    response_model=SignedUrlResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate temporary signed download URL for document",
)
async def get_document_signed_url(
    document_id: str,
    expires_in: int = Query(600, ge=300, le=3600),
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:read"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> SignedUrlResponse:
    service = GenericDocumentService(db, storage)
    url, actual_expires = await service.get_signed_url(context, document_id, expires_in=expires_in)
    return SignedUrlResponse(url=url, expires_in=actual_expires)


@router.delete(
    "/{document_id}",
    response_model=DocumentDeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete document from database, object storage, and vector store",
)
async def delete_document(
    document_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"documents:delete"})),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentDeleteResponse:
    service = GenericDocumentService(db, storage, delete_extension=delete_observations)
    vector_store = QdrantVectorStore()
    try:
        await service.delete_document(context, document_id, vector_store=vector_store)
    finally:
        await vector_store.close()

    return DocumentDeleteResponse(
        success=True,
        message=f"Document '{document_id}' successfully deleted.",
        document_id=document_id,
    )
