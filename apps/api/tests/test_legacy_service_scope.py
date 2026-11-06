import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.db import AsyncSessionLocal
from src.main import app
from src.models.service_client import ServiceClient
from src.security.service_auth import LEGACY_MEDICAL_SCOPES
from tests.conftest import VALID_AUTH_HEADER


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/clients", None),
    ("POST", "/api/v1/clients", {"client_id": "forbidden-fixture", "name": "Synthetic", "allowed_scopes": ["*"], "authorized_tenants": ["*"]}),
    ("POST", "/api/v1/clients/another-app/rotate-key", None),
    ("DELETE", "/api/v1/clients/another-app", None),
])
async def test_legacy_medical_credential_cannot_administer_platform_clients(method, path, body):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, json=body, headers=VALID_AUTH_HEADER)
    assert response.status_code == 403
    assert "clients:manage" in response.text
    assert "clients:manage" not in LEGACY_MEDICAL_SCOPES and "*" not in LEGACY_MEDICAL_SCOPES


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,status", [
    ({"status": "revoked", "revoked_at": datetime.now(UTC)}, 401),
    ({"authorized_tenants": []}, 403),
    ({"allowed_scopes": ["documents:read"]}, 403),
])
async def test_registered_profile_can_narrow_or_revoke_legacy_access(profile, status):
    async with AsyncSessionLocal() as db:
        row = ServiceClient(
            client_id="quick_clinic", name="Synthetic compatibility fixture",
            api_key_hash=uuid.uuid4().hex, api_key_prefix="synthetic",
            allowed_scopes=["*"], authorized_tenants=["quick_clinic_default"],
        )
        for field, value in profile.items():
            setattr(row, field, value)
        db.add(row)
        await db.commit()
        row_id = row.id
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/v1/collections", headers=VALID_AUTH_HEADER)
        assert response.status_code == status
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(delete(ServiceClient).where(ServiceClient.id == row_id))
            await db.commit()
