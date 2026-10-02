import os
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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


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

# CORS Configuration: strict origins in production, open in local development
allowed_origins = (
    settings.ALLOWED_ORIGINS
    if settings.ENVIRONMENT.lower() == "production"
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
