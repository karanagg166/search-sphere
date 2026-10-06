from datetime import datetime, timezone
import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.service_client import ServiceClient
from src.schemas.service_client import ServiceClientCreateRequest
from src.security.service_auth import generate_api_key

logger = structlog.get_logger()


class ServiceClientService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def register_client(
        self, request: ServiceClientCreateRequest
    ) -> tuple[ServiceClient, str]:
        # Check uniqueness of client_id
        stmt = select(ServiceClient).where(ServiceClient.client_id == request.client_id)
        res = await self.db.execute(stmt)
        if res.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Service client '{request.client_id}' already exists.",
            )

        raw_key, prefix, key_hash = generate_api_key()
        client = ServiceClient(
            client_id=request.client_id,
            name=request.name,
            api_key_hash=key_hash,
            api_key_prefix=prefix,
            status="active",
            allowed_scopes=request.allowed_scopes,
            authorized_tenants=request.authorized_tenants,
            rate_limit_per_minute=request.rate_limit_per_minute,
        )
        self.db.add(client)
        await self.db.commit()
        await self.db.refresh(client)

        logger.info(
            "Registered new service client",
            client_id=client.client_id,
            name=client.name,
            prefix=prefix,
        )
        return client, raw_key

    async def get_client_by_id(self, client_id: str) -> ServiceClient | None:
        stmt = select(ServiceClient).where(ServiceClient.client_id == client_id)
        res = await self.db.execute(stmt)
        return res.scalar_one_or_none()

    async def list_clients(self) -> list[ServiceClient]:
        stmt = select(ServiceClient).order_by(ServiceClient.created_at.desc())
        res = await self.db.execute(stmt)
        return list(res.scalars().all())

    async def rotate_key(self, client_id: str) -> tuple[ServiceClient, str]:
        client = await self.get_client_by_id(client_id)
        if not client:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service client '{client_id}' not found.",
            )
        if client.status != "active":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot rotate key for inactive or revoked client '{client_id}'.",
            )

        raw_key, prefix, key_hash = generate_api_key()
        client.api_key_hash = key_hash
        client.api_key_prefix = prefix
        client.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(client)

        logger.info(
            "Rotated service client credential",
            client_id=client_id,
            new_prefix=prefix,
        )
        return client, raw_key

    async def revoke_client(self, client_id: str) -> ServiceClient:
        client = await self.get_client_by_id(client_id)
        if not client:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Service client '{client_id}' not found.",
            )

        client.status = "revoked"
        client.revoked_at = datetime.now(timezone.utc)
        await self.db.commit()
        await self.db.refresh(client)

        logger.info("Revoked service client", client_id=client_id)
        return client
