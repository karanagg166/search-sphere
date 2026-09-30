from abc import ABC, abstractmethod
from pathlib import Path

import httpx
import structlog

from src.config import settings

logger = structlog.get_logger()


class ObjectStorage(ABC):
    """Abstract interface for worker-side object storage access."""

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Download an object by storage key."""
        raise NotImplementedError

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Return True if the object exists."""
        raise NotImplementedError


class SupabaseStorage(ObjectStorage):
    """Supabase Storage implementation for worker-side reads."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        bucket: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.NEXT_PUBLIC_SUPABASE_URL).rstrip("/")

        self.api_key = (
            settings.SUPABASE_SERVICE_ROLE_KEY
            or api_key
            or settings.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
        )

        self.bucket = bucket or settings.SUPABASE_STORAGE_BUCKET

        if not self.base_url:
            raise RuntimeError("Supabase URL is not configured.")

        if not self.api_key:
            raise RuntimeError("Supabase API key is not configured.")

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

    async def download(self, key: str) -> bytes:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(
                url,
                headers=self._headers(),
            )

        if response.status_code != 200:
            logger.error(
                "Supabase storage download failed",
                key=key,
                status_code=response.status_code,
                response=response.text,
            )

            raise RuntimeError(f"Failed to download object from Supabase: {key}")

        logger.info(
            "Downloaded document from Supabase",
            key=key,
            size=len(response.content),
        )

        return response.content

    async def exists(self, key: str) -> bool:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.head(
                url,
                headers=self._headers(),
            )

        return response.status_code == 200


class LocalStorage(ObjectStorage):
    """Local filesystem storage used for development and tests."""

    def __init__(
        self,
        base_dir: str | None = None,
        bucket: str | None = None,
    ) -> None:
        bucket_name = bucket or settings.SUPABASE_STORAGE_BUCKET

        self.base_dir = Path(base_dir or settings.LOCAL_STORAGE_DIR) / bucket_name

    async def download(self, key: str) -> bytes:
        file_path = self.base_dir / key

        if not file_path.exists():
            raise FileNotFoundError(f"Storage object not found: {key}")

        content = file_path.read_bytes()

        logger.info(
            "Downloaded document from local storage",
            key=key,
            path=str(file_path),
            size=len(content),
        )

        return content

    async def exists(self, key: str) -> bool:
        return (self.base_dir / key).exists()


def get_object_storage() -> ObjectStorage:
    """Return the configured storage backend."""

    backend = settings.STORAGE_BACKEND.lower()

    if backend == "supabase":
        return SupabaseStorage()

    if backend == "local":
        return LocalStorage()

    raise ValueError(f"Unsupported storage backend: {settings.STORAGE_BACKEND}")
