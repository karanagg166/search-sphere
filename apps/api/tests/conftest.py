import pytest

from src.db import engine


@pytest.fixture(autouse=True)
async def cleanup_db_connections():
    """Ensure database connection pool is disposed between async tests with separate event loops."""
    yield
    await engine.dispose()
