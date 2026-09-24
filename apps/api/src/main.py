import os
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.db import init_db
from src.routers.auth import router as auth_router
from src.routers.documents import router as documents_router

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
    # Shutdown
    logger.info("Shutting down Search Sphere application...")


app = FastAPI(
    title="Search Sphere API",
    description="FastAPI Backend for Search Sphere Monorepo with JWT & OAuth Authentication",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS Middleware to support frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include Application Routes
app.include_router(auth_router)
app.include_router(documents_router)


@app.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint to confirm the backend works."""
    return {
        "status": "ok",
        "service": "api",
        "version": "0.1.0",
        "environment": os.getenv("ENVIRONMENT", "development"),
    }


@app.get("/", tags=["Root"])
async def root():
    """Root endpoint with basic navigation links."""
    return {
        "message": "Search Sphere API is running.",
        "health": "/health",
        "docs": "/docs",
        "auth": "/auth",
    }
