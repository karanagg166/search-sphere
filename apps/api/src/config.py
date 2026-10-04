import json
import re

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./test.db"

    # JWT Authentication
    JWT_SECRET_KEY: str = (
        "search-sphere-super-secure-jwt-secret-key-production-ready-2026"
    )
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # Google OAuth
    GOOGLE_CLIENT_ID: str | None = ""
    GOOGLE_CLIENT_SECRET: str | None = ""
    GOOGLE_REDIRECT_URI: str | None = "http://localhost:3000/api/auth/callback/google"

    # GitHub OAuth
    GITHUB_CLIENT_ID: str | None = ""
    GITHUB_CLIENT_SECRET: str | None = ""
    GITHUB_REDIRECT_URI: str | None = ""

    # Supabase & Object Storage
    NEXT_PUBLIC_SUPABASE_URL: str = ""
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: str = ""
    SUPABASE_STORAGE_BUCKET: str = "documents"
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    STORAGE_BACKEND: str = "supabase"
    LOCAL_STORAGE_DIR: str = "./data/storage"
    MAX_UPLOAD_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MB max upload

    # Quick Clinic Service Integration
    QUICK_CLINIC_SERVICE_SECRET: str | None = None

    # Caching
    REDIS_URL: str = "redis://redis:6379/0"

    # RabbitMQ Queue & Worker
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"

    # Frontend URL (for OAuth callbacks & CORS)
    FRONTEND_URL: str = "http://localhost:3000"

    # Qdrant Vector Database
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION_NAME: str = "documents"
    QDRANT_UPSERT_BATCH_SIZE: int = 100

    # Embeddings
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_DEVICE: str = "cpu"
    FASTEMBED_CACHE_PATH: str = "/app/.fastembed_cache"

    # Sparse Retrieval (BM25)
    SPARSE_MODEL: str = "Qdrant/bm25"
    QDRANT_SPARSE_VECTOR_NAME: str = "bm25"
    SPARSE_SEARCH_TOP_K: int = 10
    SPARSE_SEARCH_MAX_TOP_K: int = 100

    # Hybrid Retrieval (Dense + BM25 with RRF)
    HYBRID_SEARCH_TOP_K: int = 10
    HYBRID_SEARCH_CANDIDATE_K: int = 20
    HYBRID_SEARCH_MAX_TOP_K: int = 100

    # Cross-Encoder Reranking
    RERANKER_MODEL_NAME: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    RERANKER_BATCH_SIZE: int = 32
    RERANKER_TOP_K: int = 5
    RERANKER_MAX_TOP_K: int = 100
    RERANKER_DEVICE: str = "cpu"
    RERANKER_SCORE_THRESHOLD: float | None = None
    CHUNK_DEDUPLICATION_ENABLED: bool = True
    CHUNK_SIMILARITY_THRESHOLD: float = 0.70

    # OCR & Image Processing
    TESSERACT_CMD: str | None = None
    IMAGE_CAPTION_MODEL: str = "Salesforce/blip-image-captioning-base"

    # Chunking
    CHUNK_TARGET_TOKENS: int = 500
    CHUNK_MAX_TOKENS: int = 700
    CHUNK_OVERLAP_TOKENS: int = 60
    SEMANTIC_CHUNKING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    SEMANTIC_DISTANCE_THRESHOLD: float = 0.5
    SEMANTIC_SIMILARITY_PERCENTILE: float = 80.0
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 60

    # Cohere & Query Rewriting
    COHERE_API_KEY: str | None = None
    QUERY_REWRITE_MODEL: str = "command-a-03-2025"
    QUERY_REWRITE_ENABLED: bool = True
    QUERY_REWRITE_TIMEOUT_SECONDS: float = 5.0
    QUERY_REWRITE_MAX_CONTEXT_MESSAGES: int = 5

    # Grounded RAG Answer Generation (Cohere)
    RAG_GENERATION_MODEL: str = "command-a-03-2025"
    RAG_GENERATION_ENABLED: bool = True
    RAG_GENERATION_TIMEOUT_SECONDS: float = 15.0
    RAG_MAX_CONTEXT_CHUNKS: int = 5
    RAG_TEMPERATURE: float = 0.1

    # Conversations
    CONVERSATION_MAX_HISTORY_MESSAGES: int = 10

    # Production Hardening & Security
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://search-sphere-rose.vercel.app",
        "https://search-sphere-karan-aggarwals-projects.vercel.app",
    ]
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_REQUESTS_PER_MINUTE: int = 120
    MAX_QUERY_LENGTH: int = 1000

    @field_validator("FRONTEND_URL", mode="before")
    @classmethod
    def clean_frontend_url(cls, v: object) -> str:
        if not v:
            return "http://localhost:3000"
        return str(v).strip().strip("'\"").rstrip("/")

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: object) -> list[str]:
        default_origins = [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://search-sphere-rose.vercel.app",
            "https://search-sphere-karan-aggarwals-projects.vercel.app",
        ]
        if not v:
            return default_origins

        origins: list[str] = []
        if isinstance(v, str):
            v = v.strip()
            if (v.startswith("[") and v.endswith("]")) or (v.startswith("(") and v.endswith(")")):
                try:
                    cleaned = v.replace("'", '"')
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, list):
                        origins = [str(x).strip().strip("'\"").rstrip("/") for x in parsed if str(x).strip()]
                except Exception:
                    pass
            if not origins:
                origins = [
                    p.strip().strip("'\"").rstrip("/")
                    for p in re.split(r"[,;\s]+", v)
                    if p.strip()
                ]
        elif isinstance(v, (list, tuple, set)):
            origins = [str(x).strip().strip("'\"").rstrip("/") for x in v if str(x).strip()]
        else:
            return default_origins

        for default in default_origins:
            if default not in origins:
                origins.append(default)

        return origins

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        """Fail clearly in production if production secrets are missing or default."""
        if self.ENVIRONMENT.lower() == "production":
            dev_defaults = (
                "search-sphere-super-secure-jwt-secret-key-production-ready-2026",
                "default_secret",
                "",
            )
            if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY in dev_defaults:
                raise ValueError(
                    "Production security failure: JWT_SECRET_KEY must be explicitly set to a unique, non-default secret in production."
                )
            if not self.COHERE_API_KEY or self.COHERE_API_KEY.strip() in (
                "",
                "your_cohere_api_key_here",
            ):
                raise ValueError(
                    "Production configuration failure: COHERE_API_KEY must be provided in production."
                )
        return self

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
