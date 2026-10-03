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
    async def create_signed_url(self, key: str, expires_in: int = 600) -> str:
        """Generates a short-lived signed URL for accessing the storage key."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Deletes an object by key. Returns True if deleted or did not exist."""
        pass

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """Downloads file content as bytes by storage key."""
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

    async def create_signed_url(self, key: str, expires_in: int = 600) -> str:
        """Generates a short-lived signed URL using Supabase Storage REST endpoint."""
        url = f"{self.base_url}/storage/v1/object/sign/{self.bucket}/{key}"
        headers = self._headers("application/json")
        body = {"expiresIn": expires_in}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code not in (200, 201):
                logger.error(
                    "Supabase signed URL generation failed",
                    status_code=resp.status_code,
                    response=resp.text,
                    key=key,
                )
                raise RuntimeError(f"Storage signed URL generation failed: {resp.text}")

            data = resp.json()
            signed_path = (
                data.get("signedURL")
                or data.get("signedUrl")
                or data.get("url")
            )
            if not signed_path:
                raise RuntimeError(f"Unexpected response from Supabase sign endpoint: {resp.text}")

            if signed_path.startswith("http://") or signed_path.startswith("https://"):
                return signed_path

            if signed_path.startswith("/storage/v1"):
                return f"{self.base_url}{signed_path}"
            elif signed_path.startswith("/"):
                return f"{self.base_url}/storage/v1{signed_path}"
            else:
                return f"{self.base_url}/storage/v1/{signed_path}"

    async def delete(self, key: str) -> bool:
        headers = self._headers("application/json")
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Try prefix-based deletion (standard Supabase Storage API)
            url_prefix = f"{self.base_url}/storage/v1/object/{self.bucket}"
            resp = await client.request(
                "DELETE", url_prefix, json={"prefixes": [key]}, headers=headers
            )
            if resp.status_code in (200, 204):
                return True

            # 2. Fallback to direct key endpoint
            url_direct = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
            resp_direct = await client.delete(url_direct, headers=self._headers())
            return resp_direct.status_code in (200, 204)

    async def download(self, key: str) -> bytes:
        url = f"{self.base_url}/storage/v1/object/{self.bucket}/{key}"
        headers = self._headers()

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.get(url, headers=headers)

        if response.status_code != 200:
            logger.error(
                "Supabase storage download failed",
                key=key,
                status_code=response.status_code,
                response=response.text,
            )
            raise RuntimeError(f"Failed to download object from Supabase ({response.status_code}): {key}")

        return response.content

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

    async def create_signed_url(self, key: str, expires_in: int = 600) -> str:
        file_path = self.base_dir / key
        if not file_path.exists():
            raise FileNotFoundError(f"Object not found in local storage: {key}")
        return f"/storage/{self.bucket}/{key}?token=mock-signed-url&expiresIn={expires_in}"

    async def delete(self, key: str) -> bool:
        file_path = self.base_dir / key
        if file_path.exists():
            file_path.unlink()
            return True
        return True

    async def download(self, key: str) -> bytes:
        file_path = self.base_dir / key
        if not file_path.exists():
            raise FileNotFoundError(f"Object not found in local storage: {key}")
        return file_path.read_bytes()

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
