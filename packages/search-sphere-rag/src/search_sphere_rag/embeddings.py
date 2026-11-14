"""Hosted embedding providers with strict space, mode, and vector validation."""

from collections.abc import Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from .contracts import (
    EmbeddingMode,
    EmbeddingResult,
    EmbeddingSpace,
    EmbeddingValidationError,
    ProviderCallError,
)
from .validation import compute_content_sha256, validate_vectors

KNOWN_EMBEDDING_DIMENSIONS: dict[tuple[str, str], int] = {
    ("cohere", "embed-english-light-v3.0"): 384,
    ("cohere", "embed-english-v3.0"): 1024,
    ("cohere", "embed-multilingual-light-v3.0"): 384,
    ("cohere", "embed-multilingual-v3.0"): 1024,
    ("google", "gemini-embedding-001"): 3072,
    ("google", "text-embedding-004"): 768,
}


@runtime_checkable
class EmbeddingProvider(Protocol):
    @property
    def space(self) -> EmbeddingSpace: ...

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult: ...

    async def embed_query(self, query: str) -> EmbeddingResult: ...


class CohereEmbeddingProvider:
    """Cohere hosted embeddings using explicit search_document / search_query modes."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "embed-english-light-v3.0",
        version: str = "v3.0",
        dimension: int | None = None,
        batch_size: int = 96,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise EmbeddingValidationError("Cohere API key is required.")
        resolved_dim = dimension or KNOWN_EMBEDDING_DIMENSIONS.get(("cohere", model))
        if resolved_dim is None or resolved_dim <= 0:
            raise EmbeddingValidationError(f"Unknown Cohere embedding dimension for model '{model}'.")
        self._api_key = api_key.strip()
        self._batch_size = max(1, min(batch_size, 96))
        self._timeout = timeout_seconds
        self._transport = transport
        self._space = EmbeddingSpace(
            provider="cohere",
            model=model,
            version=version,
            dimension=resolved_dim,
            normalized=False,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    async def _embed(self, texts: Sequence[str], mode: EmbeddingMode) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(space=self._space, mode=mode, vectors=[], input_hashes=[])

        cleaned = [str(t) for t in texts]
        if any(not t.strip() for t in cleaned):
            raise EmbeddingValidationError("Cannot embed empty or whitespace-only text.")

        input_type = "search_document" if mode == EmbeddingMode.DOCUMENT else "search_query"
        all_vectors: list[list[float]] = []

        async with httpx.AsyncClient(
            base_url="https://api.cohere.com",
            timeout=httpx.Timeout(self._timeout, connect=5.0),
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            for idx in range(0, len(cleaned), self._batch_size):
                batch = cleaned[idx : idx + self._batch_size]
                try:
                    resp = await client.post(
                        "/v2/embed",
                        json={
                            "model": self._space.model,
                            "texts": batch,
                            "input_type": input_type,
                            "embedding_types": ["float"],
                        },
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    raise ProviderCallError(f"Cohere embed request failed: {type(exc).__name__}") from exc

                if resp.status_code != 200:
                    raise ProviderCallError(f"Cohere embed rejected request with HTTP {resp.status_code}.")

                payload: dict[str, Any] = resp.json()
                raw_vecs = (payload.get("embeddings") or {}).get("float")
                if not isinstance(raw_vecs, list):
                    raise EmbeddingValidationError("Cohere embed response missing float embeddings.")
                validated = validate_vectors(
                    raw_vecs,
                    expected_count=len(batch),
                    expected_dimension=self._space.dimension,
                )
                all_vectors.extend(validated)

        return EmbeddingResult(
            space=self._space,
            mode=mode,
            vectors=all_vectors,
            input_hashes=[compute_content_sha256(t) for t in cleaned],
        )

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        return await self._embed(texts, EmbeddingMode.DOCUMENT)

    async def embed_query(self, query: str) -> EmbeddingResult:
        return await self._embed([query], EmbeddingMode.QUERY)


class GoogleEmbeddingProvider:
    """Google Gemini hosted embeddings with explicit taskType and outputDimensionality."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-embedding-001",
        version: str = "v1",
        dimension: int = 384,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise EmbeddingValidationError("Google API key is required.")
        if dimension <= 0:
            raise EmbeddingValidationError("Embedding dimension must be positive.")
        self._api_key = api_key.strip()
        self._timeout = timeout_seconds
        self._transport = transport
        self._space = EmbeddingSpace(
            provider="google",
            model=model,
            version=version,
            dimension=dimension,
            normalized=False,
        )

    @property
    def space(self) -> EmbeddingSpace:
        return self._space

    async def _embed(self, texts: Sequence[str], mode: EmbeddingMode) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(space=self._space, mode=mode, vectors=[], input_hashes=[])

        cleaned = [str(t) for t in texts]
        if any(not t.strip() for t in cleaned):
            raise EmbeddingValidationError("Cannot embed empty or whitespace-only text.")

        task_type = "RETRIEVAL_DOCUMENT" if mode == EmbeddingMode.DOCUMENT else "RETRIEVAL_QUERY"
        model_path = f"models/{self._space.model}"
        requests_body = [
            {
                "model": model_path,
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": self._space.dimension,
            }
            for text in cleaned
        ]

        async with httpx.AsyncClient(
            base_url="https://generativelanguage.googleapis.com",
            timeout=httpx.Timeout(self._timeout, connect=5.0),
            transport=self._transport,
            follow_redirects=False,
        ) as client:
            try:
                resp = await client.post(
                    f"/v1beta/{model_path}:batchEmbedContents",
                    params={"key": self._api_key},
                    json={"requests": requests_body},
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise ProviderCallError(f"Google embed request failed: {type(exc).__name__}") from exc

            if resp.status_code != 200:
                raise ProviderCallError(f"Google embed rejected request with HTTP {resp.status_code}.")

            payload: dict[str, Any] = resp.json()
            raw_items = payload.get("embeddings")
            if not isinstance(raw_items, list):
                raise EmbeddingValidationError("Google embed response missing embeddings list.")
            raw_vecs = [item.get("values") if isinstance(item, dict) else None for item in raw_items]
            validated = validate_vectors(
                raw_vecs,  # type: ignore[arg-type]
                expected_count=len(cleaned),
                expected_dimension=self._space.dimension,
            )

        return EmbeddingResult(
            space=self._space,
            mode=mode,
            vectors=validated,
            input_hashes=[compute_content_sha256(t) for t in cleaned],
        )

    async def embed_documents(self, texts: Sequence[str]) -> EmbeddingResult:
        return await self._embed(texts, EmbeddingMode.DOCUMENT)

    async def embed_query(self, query: str) -> EmbeddingResult:
        return await self._embed([query], EmbeddingMode.QUERY)
