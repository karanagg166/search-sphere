from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.schemas.collection import (
    CollectionCreateRequest,
    CollectionListResponse,
    CollectionResponse,
)
from src.security.service_auth import get_service_context
from src.security.service_context import ServiceContext
from src.services.collection_service import CollectionService

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/collections", tags=["V1 Collections Management"])


@router.post(
    "",
    response_model=CollectionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new document collection / knowledge base",
)
async def create_collection(
    body: CollectionCreateRequest,
    context: ServiceContext = Depends(get_service_context(required_scopes={"collections:manage"})),
    db: AsyncSession = Depends(get_db),
) -> CollectionResponse:
    service = CollectionService(db)
    coll = await service.create_collection(context, body)
    return CollectionResponse.model_validate(coll)


@router.get(
    "",
    response_model=CollectionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List collections in current tenant",
)
async def list_collections(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    context: ServiceContext = Depends(get_service_context(required_scopes={"collections:manage"})),
    db: AsyncSession = Depends(get_db),
) -> CollectionListResponse:
    service = CollectionService(db)
    items, total = await service.list_collections(context, limit=limit, offset=offset)
    return CollectionListResponse(
        total=total,
        collections=[CollectionResponse.model_validate(c) for c in items],
    )


@router.get(
    "/{collection_id}",
    response_model=CollectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Get collection metadata by ID",
)
async def get_collection(
    collection_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"collections:manage"})),
    db: AsyncSession = Depends(get_db),
) -> CollectionResponse:
    service = CollectionService(db)
    coll = await service.get_collection(context, collection_id)
    if not coll:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Collection '{collection_id}' not found.",
        )
    return CollectionResponse.model_validate(coll)


@router.delete(
    "/{collection_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete collection by ID",
)
async def delete_collection(
    collection_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"collections:manage"})),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    service = CollectionService(db)
    await service.delete_collection(context, collection_id)
    return {"success": True, "message": f"Collection '{collection_id}' deleted successfully."}
