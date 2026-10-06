import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.schemas.service_client import (
    ServiceClientCreatedResponse,
    ServiceClientCreateRequest,
    ServiceClientResponse,
    ServiceClientRotateResponse,
)
from src.security.service_auth import get_service_context
from src.security.service_context import ServiceContext
from src.services.service_client_service import ServiceClientService

logger = structlog.get_logger()

router = APIRouter(prefix="/api/v1/clients", tags=["V1 Client Management"])


@router.post(
    "",
    response_model=ServiceClientCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new external service client application",
    description="Creates a service client profile and issues a high-entropy API key (revealed only once).",
)
async def register_service_client(
    body: ServiceClientCreateRequest,
    context: ServiceContext = Depends(get_service_context(required_scopes={"clients:manage"})),
    db: AsyncSession = Depends(get_db),
) -> ServiceClientCreatedResponse:
    service = ServiceClientService(db)
    client, raw_key = await service.register_client(body)
    return ServiceClientCreatedResponse(
        id=client.id,
        client_id=client.client_id,
        name=client.name,
        api_key=raw_key,
        api_key_prefix=client.api_key_prefix,
        status=client.status,
        allowed_scopes=client.allowed_scopes,
        authorized_tenants=client.authorized_tenants,
        rate_limit_per_minute=client.rate_limit_per_minute,
        created_at=client.created_at,
        updated_at=client.updated_at,
        revoked_at=client.revoked_at,
    )


@router.get(
    "",
    response_model=list[ServiceClientResponse],
    status_code=status.HTTP_200_OK,
    summary="List registered external service clients",
)
async def list_service_clients(
    context: ServiceContext = Depends(get_service_context(required_scopes={"clients:manage"})),
    db: AsyncSession = Depends(get_db),
) -> list[ServiceClientResponse]:
    service = ServiceClientService(db)
    clients = await service.list_clients()
    return [
        ServiceClientResponse(
            id=c.id,
            client_id=c.client_id,
            name=c.name,
            api_key_prefix=c.api_key_prefix,
            status=c.status,
            allowed_scopes=c.allowed_scopes,
            authorized_tenants=c.authorized_tenants,
            rate_limit_per_minute=c.rate_limit_per_minute,
            created_at=c.created_at,
            updated_at=c.updated_at,
            revoked_at=c.revoked_at,
        )
        for c in clients
    ]


@router.get(
    "/{client_id}",
    response_model=ServiceClientResponse,
    status_code=status.HTTP_200_OK,
    summary="Get service client details",
)
async def get_service_client(
    client_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"clients:manage"})),
    db: AsyncSession = Depends(get_db),
) -> ServiceClientResponse:
    service = ServiceClientService(db)
    c = await service.get_client_by_id(client_id)
    if not c:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Service client '{client_id}' not found.",
        )
    return ServiceClientResponse(
        id=c.id,
        client_id=c.client_id,
        name=c.name,
        api_key_prefix=c.api_key_prefix,
        status=c.status,
        allowed_scopes=c.allowed_scopes,
        authorized_tenants=c.authorized_tenants,
        rate_limit_per_minute=c.rate_limit_per_minute,
        created_at=c.created_at,
        updated_at=c.updated_at,
        revoked_at=c.revoked_at,
    )


@router.post(
    "/{client_id}/rotate-key",
    response_model=ServiceClientRotateResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate service client API key",
)
async def rotate_service_client_key(
    client_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"clients:manage"})),
    db: AsyncSession = Depends(get_db),
) -> ServiceClientRotateResponse:
    service = ServiceClientService(db)
    client, raw_key = await service.rotate_key(client_id)
    return ServiceClientRotateResponse(
        client_id=client.client_id,
        api_key=raw_key,
        api_key_prefix=client.api_key_prefix,
        message="Credential successfully rotated. Please securely update client environment.",
    )


@router.delete(
    "/{client_id}",
    response_model=ServiceClientResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke service client access",
)
async def revoke_service_client(
    client_id: str,
    context: ServiceContext = Depends(get_service_context(required_scopes={"clients:manage"})),
    db: AsyncSession = Depends(get_db),
) -> ServiceClientResponse:
    service = ServiceClientService(db)
    c = await service.revoke_client(client_id)
    return ServiceClientResponse(
        id=c.id,
        client_id=c.client_id,
        name=c.name,
        api_key_prefix=c.api_key_prefix,
        status=c.status,
        allowed_scopes=c.allowed_scopes,
        authorized_tenants=c.authorized_tenants,
        rate_limit_per_minute=c.rate_limit_per_minute,
        created_at=c.created_at,
        updated_at=c.updated_at,
        revoked_at=c.revoked_at,
    )
