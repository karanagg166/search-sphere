from abc import ABC, abstractmethod
from pathlib import Path

import httpx
import structlog

from src.config import settings

logger = structlog.get_logger()


class ObjectStorage(ABC):
    """Abstract base class for object storage operations."""

    @abstractmethod
    async def upload(
        self, key: str, data: bytes, content_type: str = "application/pdf"
    ) -> str:
        """Uploads file content to storage and returns the accessible URL."""
        pass

    @abstractmethod
    async def get_url(self, key: str) -> str:
        """Returns the public or accessible URL for the storage key."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Deletes an object by key. Returns True if deleted or did not exist."""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Checks whether the object exists in storage."""
        pass


class SupabaseStorage(ObjectStorage):
    """Supabase Storage implementation using REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        bucket: str | None = None,
    ):
        self.base_url = (base_url or settings.NEXT_PUBLIC_SUPABASE_URL).rstrip("/")
        # Prefer service role key if available, else publishable/anon key
        self.api_key = (
            settings.SUPABASE_SERVICE_ROLE_KEY
            or api_key
            or settings.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
        )
        self.bucket = bucket or settings.SUPABASE_STORAGE_BUCKET

    def _headers(
        self, content_type: str = "application/octet-stream"
    ) -> dict[str, str]:
        headers = {
            "apikey": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def upload(
        self, key: str, data: bytes, content_type: str = "application/pdf"
    ) -> str:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
        headers = self._headers(content_type)
        headers["x-upsert"] = "true"

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, content=data, headers=headers)
            if resp.status_code not in (200, 201):
                logger.error(
                    "Supabase storage upload failed",
                    status_code=resp.status_code,
                    response=resp.text,
                    key=key,
                )
                raise RuntimeError(f"Storage upload failed: {resp.text}")

        return await self.get_url(key)

    async def get_url(self, key: str) -> str:
        return f"{self.base_url}/storage/v1/object/public/{self.bucket}/{key}"

    async def delete(self, key: str) -> bool:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
        headers = self._headers()

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(url, headers=headers)
            return resp.status_code in (200, 204)

    async def exists(self, key: str) -> bool:
        url = await self.get_url(key)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.head(url)
            return resp.status_code == 200


class LocalStorage(ObjectStorage):
    """Local filesystem object storage for testing and offline development."""

    def __init__(self, base_dir: str | None = None, bucket: str = "documents"):
        self.base_dir = Path(base_dir or settings.LOCAL_STORAGE_DIR) / bucket
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.bucket = bucket

    async def upload(
        self, key: str, data: bytes, content_type: str = "application/pdf"
    ) -> str:
        file_path = self.base_dir / key
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        logger.info("Local storage file saved", path=str(file_path), size=len(data))
        return await self.get_url(key)

    async def get_url(self, key: str) -> str:
        return f"/storage/{self.bucket}/{key}"

    async def delete(self, key: str) -> bool:
        file_path = self.base_dir / key
        if file_path.exists():
            file_path.unlink()
            return True
        return False

    async def exists(self, key: str) -> bool:
        file_path = self.base_dir / key
        return file_path.exists()


def get_object_storage() -> ObjectStorage:
    """Dependency provider returning the configured ObjectStorage backend."""
    if (
        settings.STORAGE_BACKEND.lower() == "supabase"
        and settings.NEXT_PUBLIC_SUPABASE_URL
        and "your-project" not in settings.NEXT_PUBLIC_SUPABASE_URL
    ):
        return SupabaseStorage()
    return LocalStorage()
