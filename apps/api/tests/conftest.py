from unittest.mock import patch

import pytest

from src.config import settings
from src.db import engine, init_db


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Ensure database tables are created before running tests and connection pool is cleaned."""
    import asyncio

    async def _init():
        await init_db()
        await engine.dispose()

    asyncio.run(_init())
    yield


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """Ensure database connection pool is disposed between async tests with separate event loops."""
    yield
    await engine.dispose()


TEST_SERVICE_SECRET = "test-service-secret"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer wrong-service-secret"}


@pytest.fixture
async def admin_auth_header():
    """Independent synthetic platform credential; medical credentials cannot administer clients."""
    import uuid

    from src.db import AsyncSessionLocal
    from src.models.service_client import ServiceClient
    from src.security.service_auth import generate_api_key

    raw_key, prefix, fingerprint = generate_api_key("ss_test_")
    client_id = f"test_admin_{uuid.uuid4().hex[:12]}"
    async with AsyncSessionLocal() as db:
        admin = ServiceClient(
            client_id=client_id, name="Synthetic platform administrator", api_key_hash=fingerprint,
            api_key_prefix=prefix, allowed_scopes=["clients:manage"], authorized_tenants=["_platform"],
        )
        db.add(admin)
        await db.commit()
        admin_id = admin.id
    try:
        yield {"Authorization": f"Bearer {raw_key}", "X-Tenant-ID": "_platform"}
    finally:
        from sqlalchemy import delete

        async with AsyncSessionLocal() as db:
            await db.execute(delete(ServiceClient).where(ServiceClient.id == admin_id))
            await db.commit()


@pytest.fixture(autouse=True)
def ensure_test_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured to a non-production test-only secret."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


@pytest.fixture(autouse=True)
def isolate_unit_test_queue(monkeypatch):
    """HTTP tests persist real outbox rows; only dedicated worker tests claim them."""
    monkeypatch.setattr(settings, "RUN_INGESTION_WORKER", False)
