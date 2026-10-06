from collections.abc import Iterator
from typing import Any, BinaryIO
import httpx

from search_sphere.exceptions import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    SearchSphereError,
    ServerError,
    ValidationError,
)
from search_sphere.models import (
    AnswerCitation,
    AnswerResponse,
    DocumentCollection,
    DocumentRecord,
    SearchChunkResult,
    SearchResponse,
)


class SearchSphereClient:
    """
    Official Python Client for the Search-Sphere Multi-Tenant RAG Microservice.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "http://localhost:8000",
        client_id: str | None = None,
        tenant_id: str = "default",
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client_id = client_id
        self.tenant_id = tenant_id
        self.timeout = timeout

    def _get_headers(
        self,
        tenant_id: str | None = None,
        subject_id: str | None = None,
        collection_id: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Tenant-ID": tenant_id or self.tenant_id,
        }
        if self.client_id:
            headers["X-Client-ID"] = self.client_id
        if subject_id:
            headers["X-Subject-ID"] = subject_id
        if collection_id:
            headers["X-Collection-ID"] = collection_id
        return headers

    def _handle_response(self, response: httpx.Response) -> Any:
        if response.status_code in (200, 201, 202):
            return response.json()
        if response.status_code == 401:
            raise AuthenticationError(response.text)
        if response.status_code == 403:
            raise AuthorizationError(response.text)
        if response.status_code == 404:
            raise NotFoundError(response.text)
        if response.status_code == 409:
            raise ConflictError(response.text)
        if response.status_code in (400, 422):
            raise ValidationError(response.text)
        if response.status_code >= 500:
            raise ServerError(f"Search-Sphere internal error: {response.text}")
        raise SearchSphereError(f"HTTP {response.status_code}: {response.text}")

    # ==================== Collections ====================

    def create_collection(
        self,
        collection_id: str,
        name: str,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> DocumentCollection:
        url = f"{self.base_url}/api/v1/collections"
        payload = {
            "collection_id": collection_id,
            "name": name,
            "description": description,
            "metadata": metadata,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                url,
                json=payload,
                headers=self._get_headers(tenant_id=tenant_id),
            )
            data = self._handle_response(resp)
            return DocumentCollection(**data)

    def get_collection(
        self,
        collection_id: str,
        tenant_id: str | None = None,
    ) -> DocumentCollection:
        url = f"{self.base_url}/api/v1/collections/{collection_id}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return DocumentCollection(**data)

    def list_collections(
        self,
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[DocumentCollection]:
        url = f"{self.base_url}/api/v1/collections"
        params = {"limit": limit, "offset": offset}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return [DocumentCollection(**c) for c in data.get("collections", [])]

    def delete_collection(
        self,
        collection_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        url = f"{self.base_url}/api/v1/collections/{collection_id}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.delete(url, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return data.get("success", False)

    # ==================== Documents ====================

    def upload_file(
        self,
        file: BinaryIO | bytes,
        document_id: str,
        filename: str = "document.pdf",
        content_type: str = "application/pdf",
        collection_id: str | None = None,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/v1/documents/upload"
        files = {"file": (filename, file, content_type)}
        data = {"document_id": document_id}
        if collection_id:
            data["collection_id"] = collection_id

        headers = self._get_headers(tenant_id=tenant_id)
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, files=files, data=data, headers=headers)
            return self._handle_response(resp)

    def register_document(
        self,
        external_document_id: str,
        storage_key: str,
        file_name: str,
        mime_type: str,
        file_size: int,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        document_type: str = "GENERAL",
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> DocumentRecord:
        url = f"{self.base_url}/api/v1/documents"
        payload = {
            "external_document_id": external_document_id,
            "storage_key": storage_key,
            "file_name": file_name,
            "mime_type": mime_type,
            "file_size": file_size,
            "collection_id": collection_id,
            "owner_subject_id": owner_subject_id,
            "document_type": document_type,
            "metadata": metadata,
        }
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return DocumentRecord(**data)

    def get_document(
        self,
        document_id: str,
        tenant_id: str | None = None,
    ) -> DocumentRecord:
        url = f"{self.base_url}/api/v1/documents/{document_id}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return DocumentRecord(**data)

    def list_documents(
        self,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant_id: str | None = None,
    ) -> list[DocumentRecord]:
        url = f"{self.base_url}/api/v1/documents"
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if collection_id:
            params["collection_id"] = collection_id
        if owner_subject_id:
            params["owner_subject_id"] = owner_subject_id
        if status:
            params["status"] = status

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return [DocumentRecord(**d) for d in data.get("documents", [])]

    def delete_document(
        self,
        document_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        url = f"{self.base_url}/api/v1/documents/{document_id}"
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.delete(url, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return data.get("success", False)

    def get_signed_url(
        self,
        document_id: str,
        expires_in: int = 600,
        tenant_id: str | None = None,
    ) -> str:
        url = f"{self.base_url}/api/v1/documents/{document_id}/signed-url"
        params = {"expires_in": expires_in}
        with httpx.Client(timeout=self.timeout) as client:
            resp = client.get(url, params=params, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            return data.get("url", "")

    # ==================== Semantic Search & Answers ====================

    def search(
        self,
        query: str,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        limit: int = 10,
        document_type: str | None = None,
        metadata_filters: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> SearchResponse:
        url = f"{self.base_url}/api/v1/search"
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if collection_id:
            payload["collection_id"] = collection_id
        if owner_subject_id:
            payload["owner_subject_id"] = owner_subject_id
        if document_type:
            payload["document_type"] = document_type
        if metadata_filters:
            payload["metadata_filters"] = metadata_filters

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            chunks = [SearchChunkResult(**c) for c in data.get("results", [])]
            return SearchResponse(
                query=data.get("query", query),
                total=data.get("total", len(chunks)),
                results=chunks,
                duration_ms=data.get("duration_ms", 0.0),
            )

    def generate_answer(
        self,
        query: str,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        limit: int = 5,
        system_prompt: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        metadata_filters: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> AnswerResponse:
        url = f"{self.base_url}/api/v1/answers"
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if collection_id:
            payload["collection_id"] = collection_id
        if owner_subject_id:
            payload["owner_subject_id"] = owner_subject_id
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if conversation_history:
            payload["conversation_history"] = conversation_history
        if metadata_filters:
            payload["metadata_filters"] = metadata_filters

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(url, json=payload, headers=self._get_headers(tenant_id=tenant_id))
            data = self._handle_response(resp)
            citations = [AnswerCitation(**c) for c in data.get("citations", [])]
            return AnswerResponse(
                answer=data.get("answer", ""),
                citations=citations,
                retrieved_chunk_count=data.get("retrieved_chunk_count", 0),
                duration_ms=data.get("duration_ms", 0.0),
            )

    def stream_answer(
        self,
        query: str,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        limit: int = 5,
        system_prompt: str | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        tenant_id: str | None = None,
    ) -> Iterator[str]:
        url = f"{self.base_url}/api/v1/answers/stream"
        payload: dict[str, Any] = {
            "query": query,
            "limit": limit,
        }
        if collection_id:
            payload["collection_id"] = collection_id
        if owner_subject_id:
            payload["owner_subject_id"] = owner_subject_id
        if system_prompt:
            payload["system_prompt"] = system_prompt
        if conversation_history:
            payload["conversation_history"] = conversation_history

        with httpx.stream(
            "POST",
            url,
            json=payload,
            headers=self._get_headers(tenant_id=tenant_id),
            timeout=self.timeout,
        ) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    yield line[6:]
