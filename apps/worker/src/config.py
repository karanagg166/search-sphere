from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Worker application configuration loaded from environment variables."""

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------
    DATABASE_URL: str = "sqlite+aiosqlite:///./test.db"

    # ------------------------------------------------------------------
    # RabbitMQ / Dramatiq
    # ------------------------------------------------------------------
    RABBITMQ_URL: str = "amqp://guest:guest@rabbitmq:5672/"
    DOCUMENT_PROCESSING_QUEUE: str = "default"

    # ------------------------------------------------------------------
    # Object Storage
    # ------------------------------------------------------------------
    STORAGE_BACKEND: str = "supabase"

    NEXT_PUBLIC_SUPABASE_URL: str = ""
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: str = ""
    SUPABASE_SERVICE_ROLE_KEY: str | None = None
    SUPABASE_STORAGE_BUCKET: str = "documents"

    LOCAL_STORAGE_DIR: str = "./data/storage"

    # ------------------------------------------------------------------
    # Document Processing
    # ------------------------------------------------------------------
    TEMP_DOCUMENT_DIR: str = "/tmp/search-sphere"

    # OCR
    TESSERACT_CMD: str | None = None

    # Vision / Image Captioning
    IMAGE_CAPTION_MODEL: str = "Salesforce/blip-image-captioning-base"

    # ------------------------------------------------------------------
    # Chunking
    # Hybrid structure-aware & semantic chunking configuration.
    # Defaults are initial experimental baselines to be tuned via retrieval eval.
    # ------------------------------------------------------------------
    CHUNK_TARGET_TOKENS: int = 500
    CHUNK_MAX_TOKENS: int = 700
    CHUNK_OVERLAP_TOKENS: int = 60
    SEMANTIC_CHUNKING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    SEMANTIC_DISTANCE_THRESHOLD: float = 0.5
    SEMANTIC_SIMILARITY_PERCENTILE: float = 80.0

    # Backwards-compatible aliases
    CHUNK_SIZE: int = 500
    CHUNK_OVERLAP: int = 60

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------
    EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_DEVICE: str = "cpu"

    # ------------------------------------------------------------------
    # Qdrant Vector Database
    # ------------------------------------------------------------------
    QDRANT_URL: str = "http://qdrant:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION_NAME: str = "documents"
    QDRANT_UPSERT_BATCH_SIZE: int = 100

    # ------------------------------------------------------------------
    # Redis
    # Redis is NOT the worker queue anymore.
    # Keep configuration available for future caching/temporary state.
    # ------------------------------------------------------------------
    REDIS_URL: str = "redis://redis:6379/0"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
