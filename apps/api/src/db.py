import os

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from src.config import settings

logger = structlog.get_logger()

# Use pool_pre_ping to automatically reconnect if dropped by Supabase/PgBouncer
engine_kwargs = {
    "echo": False,
    "hide_parameters": True,
    "pool_pre_ping": True,
}
if "sqlite" not in settings.DATABASE_URL:
    engine_kwargs.update({
        "pool_recycle": 300,
        "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
        "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "5")),
    })
if "asyncpg" in settings.DATABASE_URL:
    engine_kwargs["connect_args"] = {"timeout": 15.0, "command_timeout": 15.0}

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency that yields an active database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """Creates database tables if they do not already exist."""
    # Ensure all ORM models are registered on Base.metadata before creating tables
    from sqlalchemy import text

    import src.models  # noqa: F401

    try:
        async with engine.begin() as conn:
            if "postgresql" in engine.dialect.name.lower():
                try:
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                except Exception as ext_err:
                    logger.warning("Could not create vector extension in init_db", error=str(ext_err))
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize database tables", error=str(e))
        raise
