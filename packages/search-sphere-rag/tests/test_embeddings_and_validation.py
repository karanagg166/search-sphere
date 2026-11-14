"""Tests for vector validation, embedding space contracts, and hosted providers."""

import json

import httpx
import pytest

from search_sphere_rag import (
    CohereEmbeddingProvider,
    EmbeddingMode,
    EmbeddingSpace,
    EmbeddingSpaceMismatchError,
    EmbeddingValidationError,
    GoogleEmbeddingProvider,
    validate_vectors,
)


def test_validate_vectors_rejects_mismatched_dimensions_without_padding() -> None:
    with pytest.raises(EmbeddingValidationError, match="dimension mismatch"):
        validate_vectors([[0.1, 0.2]], expected_count=1, expected_dimension=4)


def test_validate_vectors_rejects_nan_inf_and_bool() -> None:
    with pytest.raises(EmbeddingValidationError, match="Non-finite"):
        validate_vectors([[float("nan"), 0.2]], expected_count=1, expected_dimension=2)
    with pytest.raises(EmbeddingValidationError, match="Non-finite"):
        validate_vectors([[float("inf"), 0.2]], expected_count=1, expected_dimension=2)
    with pytest.raises(EmbeddingValidationError, match="Non-numeric"):
        validate_vectors([[True, 0.2]], expected_count=1, expected_dimension=2)  # type: ignore[list-item]


def test_embedding_space_mismatch_rejected() -> None:
    cohere_space = EmbeddingSpace(provider="cohere", model="embed-english-light-v3.0", dimension=384)
    google_space = EmbeddingSpace(provider="google", model="gemini-embedding-001", dimension=384)
    with pytest.raises(EmbeddingSpaceMismatchError):
        cohere_space.assert_compatible(google_space)


@pytest.mark.asyncio
async def test_cohere_provider_uses_distinct_document_and_query_modes() -> None:
    seen_modes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        seen_modes.append(body["input_type"])
        vecs = [[0.1] * 384 for _ in body["texts"]]
        return httpx.Response(200, json={"embeddings": {"float": vecs}})

    provider = CohereEmbeddingProvider(
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    doc_res = await provider.embed_documents(["Hemoglobin 13.5 g/dL"])
    query_res = await provider.embed_query("What is my hemoglobin?")

    assert seen_modes == ["search_document", "search_query"]
    assert doc_res.mode == EmbeddingMode.DOCUMENT
    assert query_res.mode == EmbeddingMode.QUERY
    assert len(doc_res.vectors[0]) == 384
    assert len(doc_res.input_hashes[0]) == 64


@pytest.mark.asyncio
async def test_google_provider_validates_dimension_and_task_type() -> None:
    seen_tasks: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        seen_tasks.append(body["requests"][0]["taskType"])
        vecs = [{"values": [0.2] * 384} for _ in body["requests"]]
        return httpx.Response(200, json={"embeddings": vecs})

    provider = GoogleEmbeddingProvider(
        api_key="test-key",
        dimension=384,
        transport=httpx.MockTransport(handler),
    )
    await provider.embed_documents(["Glucose 98 mg/dL"])
    await provider.embed_query("Check glucose")
    assert seen_tasks == ["RETRIEVAL_DOCUMENT", "RETRIEVAL_QUERY"]
