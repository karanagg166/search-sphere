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
