import asyncio
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from src.config import settings
from src.db import AsyncSessionLocal, engine, init_db
from src.routers.answer import router as answer_router
from src.routers.auth import router as auth_router
from src.routers.conversations import router as conversations_router
from src.routers.documents import router as documents_router
from src.routers.feedback import router as feedback_router
from src.routers.internal_medical_documents import router as internal_medical_docs_router
from src.routers.search import router as search_router

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Initialize database tables
    logger.info("Initializing Search Sphere application...")
    try:
        await init_db()
    except Exception as e:
        logger.error("Failed to run init_db on startup", error=str(e))

    # Auto-index any uploaded documents in background
    try:
        from src.services.document_indexer import sync_unindexed_documents

        asyncio.create_task(sync_unindexed_documents())
    except Exception as e:
        logger.warning("Could not launch sync_unindexed_documents on startup", error=str(e))

    yield
    # Shutdown: Dispose database connections gracefully
    logger.info("Shutting down Search Sphere application...")
    try:
        await engine.dispose()
        logger.info("Database connection pool disposed gracefully.")
    except Exception as e:
        logger.error("Error disposing database connection pool", error=str(e))


class RequestCorrelationMiddleware(BaseHTTPMiddleware):
    """Assigns or propagates X-Request-ID and binds it to structured logging context."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        except Exception as exc:
            logger.error("Unhandled error in request middleware", error=str(exc), path=request.url.path)
            res = JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "An internal server error occurred. Please try again later."},
            )
            res.headers["X-Request-ID"] = request_id
            return res


app = FastAPI(
    title="Search Sphere API",
    description="FastAPI Backend for Search Sphere Monorepo with JWT & OAuth Authentication",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Request Correlation ID Middleware
app.add_middleware(RequestCorrelationMiddleware)

# CORS Configuration: strict environment-configured origins + wildcard regex for all Vercel domains
cors_origins = list(settings.ALLOWED_ORIGINS)
if settings.FRONTEND_URL:
    clean_frontend = settings.FRONTEND_URL.rstrip("/")
    if clean_frontend not in cors_origins:
        cors_origins.append(clean_frontend)

for default_origin in [
    "https://search-sphere-rose.vercel.app",
    "https://search-sphere-karan-aggarwals-projects.vercel.app",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]:
    if default_origin not in cors_origins:
        cors_origins.append(default_origin)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_origin_regex=r"^https://([a-zA-Z0-9_-]+\.)?vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=86400,
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Global exception handler to mask internal server errors while preserving structured logs."""
    logger.error("Unhandled internal server error", error=str(exc), path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred. Please try again later."},
    )


# Include Application Routes
app.include_router(auth_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(answer_router)
app.include_router(conversations_router)
app.include_router(feedback_router)
app.include_router(internal_medical_docs_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint to confirm the backend process is running."""
    return {
        "status": "ok",
        "service": "api",
        "version": "0.1.0",
        "environment": settings.ENVIRONMENT,
    }


@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness probe verifying critical infrastructure connectivity (database)."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {
            "status": "ready",
            "database": "connected",
            "environment": settings.ENVIRONMENT,
        }
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "database": "disconnected",
                "error": "Database connectivity check failed.",
            },
        )


@app.get("/diag", tags=["Health"])
async def diagnostic_check():
    """Diagnostic check of FastEmbed models and Qdrant connectivity."""
    import time
    res: dict = {"database": "ok", "fastembed": "unknown", "qdrant": "unknown"}
    try:
        t0 = time.perf_counter()
        from fastembed import TextEmbedding
        cache_path = getattr(settings, "FASTEMBED_CACHE_PATH", None)
        emb = TextEmbedding(model_name="sentence-transformers/all-MiniLM-L6-v2", cache_dir=cache_path)
        vecs = list(emb.embed(["ping"]))
        res["fastembed"] = f"ok ({len(vecs[0])} dims, {(time.perf_counter() - t0)*1000:.1f}ms)"
    except Exception as exc:
        res["fastembed"] = f"error: {exc}"

    try:
        from src.vector_store.qdrant_store import QdrantVectorStore
        store = QdrantVectorStore()
        info = store.client.get_collection(store.collection_name)
        res["qdrant"] = f"ok (collection '{store.collection_name}', {info.points_count} points)"
    except Exception as exc:
        res["qdrant"] = f"error: {exc}"

    return res


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with basic navigation links."""
    return {
        "message": "Search Sphere API is running.",
        "health": "/health",
        "ready": "/ready",
        "docs": "/docs",
        "auth": "/auth",
        "search": "/search",
        "answer": "/answer",
    }
