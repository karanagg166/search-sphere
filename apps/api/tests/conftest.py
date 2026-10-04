import pytest

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


from unittest.mock import patch
from src.config import settings

TEST_SERVICE_SECRET = "test-service-secret"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer wrong-service-secret"}


@pytest.fixture(autouse=True)
def ensure_test_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured to a non-production test-only secret."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield
