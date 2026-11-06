from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.schemas.document import DocumentListResponse, DocumentResponse
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter
from src.services.document_access import private_document_response
from src.services.document_service import DocumentService
from src.storage.object_storage import ObjectStorage, get_object_storage

router = APIRouter(prefix="/documents", tags=["Documents"])


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter.check)],
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


@router.post(
    "/sync-unindexed",
    status_code=status.HTTP_200_OK,
    summary="Sync all unindexed documents",
    description="Scan and index any uploaded documents that are pending indexing.",
)
async def sync_all_unindexed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> dict:
    queued = await DocumentService(db, storage).queue_pending_user_documents(current_user.id)
    return {
        "message": "Owned pending documents queued",
        "indexed_count": 0,
        "indexed_ids": [],
        "queued_count": len(queued),
        "queued_ids": queued,
    }


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


@router.get("/{document_id}/access", summary="Get an authorized private document download")
async def get_document_access(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> dict[str, str | int]:
    service = DocumentService(db=db, storage=storage)
    document = await service.get_user_document(document_id=document_id, user_id=current_user.id)
    response = await private_document_response(document, storage)
    return {"url": response.file_url, "expires_in": 600}


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


@router.get("/{document_id}/cleanup", summary="Verify owned document vector and object cleanup")
async def get_document_cleanup(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from src.repositories.document_deletion import cleanup_receipt, find_deletion

    row = await find_deletion(db, "standalone", "search_sphere", current_user.id, document_id)
    return await cleanup_receipt(db, row)


@router.post(
    "/{document_id}/reindex",
    response_model=DocumentResponse,
    status_code=status.HTTP_200_OK,
    summary="Reindex document",
    description="Queue durable re-extraction and indexing for an owned document.",
)
async def reindex_document(
    document_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    service = DocumentService(db=db, storage=storage)
    document = await service.reindex_user_document(document_id, current_user.id)
    return DocumentResponse.model_validate(document)


@router.get("/{document_id}/versions", summary="List authorized published index versions")
async def get_document_versions(
    document_id: str, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> dict:
    from src.services.document_versions import list_published_versions

    document = await DocumentService(db, storage).get_user_document(document_id, current_user.id)
    return {"current_version_id": document.current_version_id,
        "versions": await list_published_versions(db, "standalone", document.id, limit, offset)}


@router.get("/{document_id}/evidence/{evidence_id}", summary="Read authorized full structured evidence")
async def get_document_evidence(
    document_id: str, evidence_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> dict:
    from src.services.structured_evidence import read_structured_evidence

    document = await DocumentService(db, storage).get_user_document(document_id, current_user.id)
    return await read_structured_evidence(db, "standalone", document.id, evidence_id)


@router.get("/{document_id}/evidence/{evidence_id}/crop", summary="Read an authorized private source crop")
async def get_document_evidence_crop(
    document_id: str, evidence_id: str,
    asset_id: str | None = Query(None, max_length=36),
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
) -> dict:
    from src.services.structured_evidence import structured_crop_access

    document = await DocumentService(db, storage).get_user_document(document_id, current_user.id)
    return await structured_crop_access(db, storage, "standalone", document.id, evidence_id, asset_id)
