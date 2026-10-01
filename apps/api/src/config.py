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

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
