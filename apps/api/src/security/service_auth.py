import hmac
from fastapi import Header, HTTPException, status
import structlog

from src.config import settings

logger = structlog.get_logger()


def verify_service_secret(authorization: str | None = Header(None)) -> bool:
    """
    Centralized server-to-server internal service authentication for Quick Clinic <-> Search Sphere.

    Requirements:
    - Missing auth -> 401
    - Malformed auth -> 401
    - Incorrect secret -> 401 (constant-time comparison)
    - Missing server configuration -> fail closed (500)
    - Secrets are NEVER logged
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

    expected_secret = settings.QUICK_CLINIC_SERVICE_SECRET
    if not expected_secret:
        logger.error("Internal service secret is not configured on server (failing closed)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server configuration error: service secret is not configured.",
        )

    if not hmac.compare_digest(token.encode("utf-8"), expected_secret.encode("utf-8")):
        logger.warning("Invalid internal service secret attempt on medical API")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service secret.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True
