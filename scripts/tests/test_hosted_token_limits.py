"""Exact hosted token limits with synthetic responses; no real provider calls."""

import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from src.config import settings
from src.inference.cohere_gateway import EMBEDDING_MODEL, CohereGateway
from src.inference.policy import (
    ProviderError,
    ProviderInvalidResponseError,
    ProviderNotApprovedError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
    inference_scope,
)
from src.inference.usage import CohereUsageBudget
from src.processing.chunking.hosted_token_limits import fit_hosted_chunks
from src.processing.chunking.semantic_splitter import SemanticSplitter
from src.processing.chunking.token_counter import TokenCounter
from src.processing.models.document import ChunkedDocument, DocumentChunk

KEY = "synthetic-tokenizer-fixture-only"


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setattr(settings, "COHERE_TRIAL_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    monkeypatch.setattr(settings, "COHERE_APPROVED_DATA_CLASSIFICATIONS", ["public", "synthetic"])
    monkeypatch.setattr(settings, "COHERE_SENSITIVE_APPROVAL_REF", "")


def gateway(handler, budget=None):
    return CohereGateway(KEY, transport=httpx.MockTransport(handler), budget=budget or SimpleNamespace(reserve=AsyncMock()))


@pytest.mark.asyncio
async def test_exact_count_cache_is_request_scoped_and_reauthorizes_hits():
    calls = []
    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"tokens": [1, 2, 3]})
    client = gateway(response)
    validator = AsyncMock()
    with inference_scope("synthetic", "client", "tenant", validator=validator):
        assert await client.token_count("PUBLIC SYNTHETIC") == 3
        assert await client.token_count("PUBLIC SYNTHETIC") == 3
        assert len(calls) == 1 and validator.await_count == 2
        validator.side_effect = ProviderNotApprovedError("Synthetic revocation")
        with pytest.raises(ProviderNotApprovedError):
            await client.token_count("PUBLIC SYNTHETIC")
    with inference_scope("synthetic", "client", "another-tenant"):
        assert await client.token_count("PUBLIC SYNTHETIC") == 3
    assert len(calls) == 2 and all(call["model"] == EMBEDDING_MODEL for call in calls)


@pytest.mark.asyncio
async def test_tokenizer_denies_sensitive_data_before_http():
    client = gateway(lambda request: pytest.fail("Sensitive data must not be transmitted"))
    with inference_scope("medical", "client", "patient"), pytest.raises(ProviderNotApprovedError):
        await client.token_count("SYNTHETIC PLACEHOLDER")
    client.budget.reserve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [None, [], [True], [-1], ["one"]])
async def test_invalid_tokenizer_response_never_falls_back(tokens):
    client = gateway(lambda request: httpx.Response(200, json={"tokens": tokens}))
    with inference_scope("synthetic", "client", "tenant"), pytest.raises(ProviderInvalidResponseError):
        await client.token_count("PUBLIC SYNTHETIC")


@pytest.mark.asyncio
async def test_semantic_features_fit_without_truncating_document_evidence():
    calls = []
    def response(request):
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("tokenize"):
            return httpx.Response(200, json={"tokens": [1] * len(payload["text"])})
        return httpx.Response(200, json={"embeddings": {"float": [[1.0] + [0.0] * 383 for _ in payload["texts"]]}})
    text = "PUBLIC-SYNTHETIC-" * 100
    with inference_scope("synthetic", "client", "tenant"):
        vectors = await gateway(response).semantic_embed([text])
    assert len(vectors) == 1 and text == "PUBLIC-SYNTHETIC-" * 100
    embedded = calls[-1][1]
    assert 0 < len(embedded["texts"][0]) <= 512 and embedded["truncate"] == "NONE"


@pytest.mark.asyncio
async def test_final_chunks_are_verified_and_preserve_all_words_and_source_links():
    text = " ".join(f"public-ID-{number}" for number in range(180))
    source = {"block_id": "public-synthetic-source", "page_number": 1, "bbox": [0, 0, 100, 100]}
    original = DocumentChunk(0, text, 700, 1, 1, [1], ["text"], [source])
    counter = SimpleNamespace(token_count=AsyncMock(side_effect=lambda value, **kwargs: len(value)))
    fitted = await fit_hosted_chunks(ChunkedDocument([original]), counter)
    assert len(fitted.chunks) > 1
    assert " ".join(chunk.content for chunk in fitted.chunks) == text
    assert [chunk.chunk_index for chunk in fitted.chunks] == list(range(len(fitted.chunks)))
    assert all(chunk.token_count <= 512 and chunk.provenance == [source] for chunk in fitted.chunks)
    assert original.content == text and original.token_count == 700


def test_coarse_token_splits_preserve_unicode_without_replacement_characters():
    original = "🙂🧬漢字" * 100
    segments = TokenCounter().split_by_tokens(original, 12)
    assert "".join(segments) == original
    assert all("\ufffd" not in part and TokenCounter().count(part) <= 12 for part in segments)


def test_large_documents_bound_actual_adjacent_comparisons():
    seen = []
    def embed(texts):
        seen.extend(texts)
        return [[1.0, 0.0] for _ in texts]
    splitter = SemanticSplitter(embedder=SimpleNamespace(embed=embed))
    assert splitter.find_semantic_boundaries([f"PUBLIC paragraph {index}" for index in range(1000)]) == set()
    assert len(seen) <= 32
    assert seen[0] == "PUBLIC paragraph 0" and seen[-1] == "PUBLIC paragraph 999"


@pytest.mark.asyncio
async def test_background_admission_can_retry_reservation_without_repeating_http():
    budget = SimpleNamespace(
        reserve=AsyncMock(side_effect=[ProviderRateLimitError("before HTTP"), None]),
        wait_for_tokenize_slot=AsyncMock(),
    )
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(200, json={"tokens": [1]})
    with inference_scope("synthetic", "client", "tenant"):
        assert await gateway(response, budget).token_count("PUBLIC", background=True) == 1
    assert len(requests) == 1 and budget.wait_for_tokenize_slot.await_count == 2


@pytest.mark.asyncio
async def test_provider_quota_response_is_not_automatically_retried():
    requests = []
    def response(request):
        requests.append(request)
        return httpx.Response(429)
    with inference_scope("synthetic", "client", "tenant"), pytest.raises(ProviderQuotaExceededError):
        await gateway(response).token_count("PUBLIC", background=True)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_background_admission_waits_without_reserving_or_sending(monkeypatch):
    read = AsyncMock(side_effect=[SimpleNamespace(used=80), None])
    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(get=read)
    sleep = AsyncMock()
    monkeypatch.setattr("src.inference.usage.asyncio.sleep", sleep)
    await CohereUsageBudget(sessions).wait_for_tokenize_slot(KEY)
    assert read.await_count == 2 and sleep.await_count == 1
    assert 0 < sleep.await_args.args[0] <= 60


@pytest.mark.asyncio
async def test_background_admission_fails_closed_when_usage_db_is_unavailable():
    @asynccontextmanager
    async def sessions():
        raise RuntimeError("synthetic database outage")
        yield
    with pytest.raises(ProviderError, match="budget verification is unavailable"):
        await CohereUsageBudget(sessions).wait_for_tokenize_slot(KEY)
