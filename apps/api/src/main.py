import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

logger = structlog.get_logger()

app = FastAPI(
    title="Semantic Search & RAG API",
    description="FastAPI Backend for Semantic Search and RAG Monorepo",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS Middleware to support frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "message": "Semantic Search / RAG API is running.",
        "health": "/health",
        "docs": "/docs",
    }
