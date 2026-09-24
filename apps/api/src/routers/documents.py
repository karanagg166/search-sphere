from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.schemas.document import DocumentListResponse, DocumentResponse
from src.security.jwt import get_current_user
from src.services.document_service import DocumentService
from src.storage.object_storage import ObjectStorage, get_object_storage

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload PDF document",
    description="Upload a PDF file. Validates magic bytes, persists to object storage, stores metadata in PostgreSQL, and enqueues background processing.",
)
async def upload_document(
    file: UploadFile = File(..., description="The PDF document file to upload"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    service = DocumentService(db=db, storage=storage)
    document = await service.upload_document(file=file, user=current_user)
    return DocumentResponse.model_validate(document)


@router.get(
    "",
    response_model=DocumentListResponse,
    status_code=status.HTTP_200_OK,
    summary="List user documents",
    description="Retrieve all documents uploaded by the authenticated user.",
)
async def list_documents(
    limit: int = Query(50, ge=1, le=100, description="Maximum documents to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentListResponse:
    service = DocumentService(db=db, storage=storage)
    documents, total = await service.list_user_documents(
        user_id=current_user.id, limit=limit, offset=offset
    )
    return DocumentListResponse(
        total=total,
        documents=[DocumentResponse.model_validate(doc) for doc in documents],
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Get document details",
    description="Retrieve metadata for a specific document owned by the user.",
)
async def get_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    service = DocumentService(db=db, storage=storage)
    document = await service.get_user_document(
        document_id=document_id, user_id=current_user.id
    )
    return DocumentResponse.model_validate(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete document",
    description="Delete a document from both Object Storage and PostgreSQL.",
)
async def delete_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> None:
    service = DocumentService(db=db, storage=storage)
    await service.delete_user_document(document_id=document_id, user_id=current_user.id)
