import hashlib
import hmac
import secrets
from typing import Any, Callable

import structlog
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import AsyncSessionLocal, get_db
from src.models.service_client import ServiceClient
from src.security.service_context import ServiceContext

logger = structlog.get_logger()


def hash_api_key(token: str) -> str:
    """Computes SHA-256 hex digest of a raw API key / service token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "ss_live_") -> tuple[str, str, str]:
    """
    Generates a secure high-entropy API key.
    Returns: (raw_key, key_prefix, key_hash)
    """
    random_part = secrets.token_urlsafe(32)
    raw_key = f"{prefix}{random_part}"
    key_prefix = raw_key[:12]
    key_hash = hash_api_key(raw_key)
    return raw_key, key_prefix, key_hash


async def _authenticate_service_context(
    request: Request,
    authorization: str | None = Header(None),
    x_client_id: str | None = Header(None, alias="X-Client-ID"),
    x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
    x_subject_id: str | None = Header(None, alias="X-Subject-ID"),
    x_collection_id: str | None = Header(None, alias="X-Collection-ID"),
    session: AsyncSession = Depends(get_db),
) -> ServiceContext:
    """
    Authoritative server-to-server microservice authentication dependency.

    Enforces:
    1. Bearer token extraction and constant-time validation.
    2. Multi-client credential lookup (hashed tokens).
    3. Backward-compatible fallback for QUICK_CLINIC_SERVICE_SECRET.
    4. Tenant boundary authorization verification.
    5. Client ID match verification if supplied in header.
    6. Never logs secrets or raw tokens.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization scheme. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token cannot be empty.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 1. Backward-compatible fallback: Quick-Clinic master secret
    legacy_secret = settings.QUICK_CLINIC_SERVICE_SECRET
    if legacy_secret and hmac.compare_digest(token.encode("utf-8"), legacy_secret.encode("utf-8")):
        tenant_id = (x_tenant_id or "quick_clinic_default").strip()
        client_id = (x_client_id or "quick_clinic").strip()
        return ServiceContext(
            client_id=client_id,
            tenant_id=tenant_id,
            subject_id=x_subject_id.strip() if x_subject_id else None,
            collection_id=x_collection_id.strip() if x_collection_id else None,
            scopes={"*"},
            client_name="Quick Clinic Legacy Client",
            is_service_client=True,
        )

    # 2. Database-backed multi-client authentication
    token_hash = hash_api_key(token)
    stmt = select(ServiceClient).where(
        ServiceClient.api_key_hash == token_hash,
        ServiceClient.status == "active",
        ServiceClient.revoked_at.is_(None),
    )
    result = await session.execute(stmt)
    client: ServiceClient | None = result.scalar_one_or_none()

    if not client:
        logger.warning("Unrecognized, inactive, or revoked service credential attempt")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked service credentials.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Client identity verification
    if x_client_id and x_client_id.strip() != client.client_id:
        logger.warning(
            "Service client ID mismatch",
            header_client=x_client_id.strip(),
            token_client=client.client_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Header X-Client-ID does not match authenticated service credential.",
        )

    # 4. Tenant scope authorization verification
    tenant_id = (x_tenant_id or "default").strip()
    if not client.is_tenant_authorized(tenant_id):
        logger.warning(
            "Service client attempted unauthorized tenant access",
            client_id=client.client_id,
            tenant_id=tenant_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Client '{client.client_id}' is not authorized to access tenant '{tenant_id}'.",
        )

    return ServiceContext(
        client_id=client.client_id,
        tenant_id=tenant_id,
        subject_id=x_subject_id.strip() if x_subject_id else None,
        collection_id=x_collection_id.strip() if x_collection_id else None,
        scopes=set(client.allowed_scopes),
        client_name=client.name,
        is_service_client=True,
    )


class ServiceContextDependency:
    """FastAPI dependency callable that validates credentials and enforces required scopes."""

    def __init__(self, required_scopes: set[str] | list[str] | None = None):
        self.required_scopes = set(required_scopes) if required_scopes else set()

    async def __call__(
        self,
        request: Request,
        authorization: str | None = Header(None),
        x_client_id: str | None = Header(None, alias="X-Client-ID"),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_subject_id: str | None = Header(None, alias="X-Subject-ID"),
        x_collection_id: str | None = Header(None, alias="X-Collection-ID"),
        session: AsyncSession = Depends(get_db),
    ) -> ServiceContext:
        context = await _authenticate_service_context(
            request=request,
            authorization=authorization,
            x_client_id=x_client_id,
            x_tenant_id=x_tenant_id,
            x_subject_id=x_subject_id,
            x_collection_id=x_collection_id,
            session=session,
        )
        for scope in self.required_scopes:
            context.require_scope(scope)
        return context


def get_service_context(
    required_scopes: set[str] | list[str] | None = None,
) -> ServiceContextDependency:
    """FastAPI dependency factory for multi-tenant microservice authentication."""
    return ServiceContextDependency(required_scopes=required_scopes)


async def verify_service_secret(
    authorization: str | None = Header(None),
    session: AsyncSession = Depends(get_db),
) -> bool:
    """
    Backward-compatibility verification for existing Quick-Clinic / internal endpoints.
    Accepts either the legacy QUICK_CLINIC_SERVICE_SECRET or a valid active ServiceClient token.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization scheme. Bearer token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer token cannot be empty.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Check legacy secret
    legacy_secret = settings.QUICK_CLINIC_SERVICE_SECRET
    if legacy_secret and hmac.compare_digest(token.encode("utf-8"), legacy_secret.encode("utf-8")):
        return True

    # Check database-backed service clients
    token_hash = hash_api_key(token)
    stmt = select(ServiceClient).where(
        ServiceClient.api_key_hash == token_hash,
        ServiceClient.status == "active",
        ServiceClient.revoked_at.is_(None),
    )
    result = await session.execute(stmt)
    client = result.scalar_one_or_none()
    if client:
        return True

    # If neither legacy secret nor database client matches
    if not legacy_secret:
        logger.error("Internal service secret is not configured on server (failing closed)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: service secret is not configured.",
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid service secret.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_scopes(*required_scopes: str) -> ServiceContextDependency:
    """FastAPI dependency factory enforcing that caller has all specified scopes."""
    return ServiceContextDependency(required_scopes=set(required_scopes))
