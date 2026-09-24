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

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )


settings = Settings()
