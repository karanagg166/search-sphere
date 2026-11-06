import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from src.config import settings
from src.inference.cohere_gateway import EMBEDDING_DIMENSION, CohereGateway
from src.inference.policy import (
    DataClassification,
    ProviderError,
    ProviderInvalidResponseError,
    ProviderNotApprovedError,
    ProviderQuotaExceededError,
    inference_scope,
)

KEY = "synthetic-test-provider-key"


@pytest.fixture(autouse=True)
def policy(monkeypatch):
    monkeypatch.setattr(
        settings, "COHERE_TRIAL_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest()
    )
    monkeypatch.setattr(
        settings, "COHERE_APPROVED_DATA_CLASSIFICATIONS", ["public", "synthetic"]
    )
    monkeypatch.setattr(settings, "COHERE_SENSITIVE_APPROVAL_REF", "")


def gateway(handler):
    budget = SimpleNamespace(reserve=AsyncMock())
    return CohereGateway(KEY, transport=httpx.MockTransport(handler), budget=budget)


@pytest.mark.asyncio
async def test_no_scope_or_sensitive_scope_fails_before_network_or_quota():
    requests = []
    client = gateway(lambda request: requests.append(request))
    with pytest.raises(ProviderNotApprovedError):
        await client.embed(["sensitive fixture placeholder"])
    for classification in (DataClassification.CONFIDENTIAL, DataClassification.MEDICAL):
        with (
            inference_scope(classification, "synthetic-client", "synthetic-tenant"),
            pytest.raises(ProviderNotApprovedError),
        ):
            await client.embed(["sensitive fixture placeholder"])
    assert requests == []
    client.budget.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_key_cannot_reuse_a_trial_approval():
    client = gateway(lambda request: pytest.fail("must not send content"))
    client.api_key = "different-unverified-key"
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ProviderNotApprovedError),
    ):
        await client.embed(["public synthetic input"])


@pytest.mark.asyncio
async def test_sensitive_opt_in_requires_a_separate_approval_reference(monkeypatch):
    monkeypatch.setattr(settings, "COHERE_APPROVED_DATA_CLASSIFICATIONS", ["medical"])
    client = gateway(lambda request: pytest.fail("must not send content"))
    with (
        inference_scope(DataClassification.MEDICAL, "fixture", "fixture"),
        pytest.raises(ProviderNotApprovedError, match="approval is not recorded"),
    ):
        await client.embed(["synthetic placeholder"])


@pytest.mark.asyncio
async def test_batched_document_and_query_embeddings_share_a_version_without_truncation():
    import json

    calls = []

    def response(request):
        payload = json.loads(request.content)
        calls.append(payload)
        vectors = [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in payload["texts"]]
        return httpx.Response(200, json={"embeddings": {"float": vectors}})

    client = gateway(response)
    with inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"):
        documents = await client.embed([f"PUBLIC-FIXTURE-{i}" for i in range(65)])
        query = await client.embed(["PUBLIC-FIXTURE-1"], query=True)
    assert len(documents) == 65 and len(query) == 1
    assert [len(call["texts"]) for call in calls] == [32, 32, 1, 1]
    assert {call["model"] for call in calls} == {"embed-english-light-v3.0"}
    assert all(call["truncate"] == "NONE" for call in calls)
    assert calls[-1]["input_type"] == "search_query"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "vector", [[], [0.0] * 384, [float("nan")] * 384, [True] * 384]
)
async def test_invalid_embeddings_are_rejected(vector):
    client = gateway(
        lambda request: httpx.Response(
            200, content=json.dumps({"embeddings": {"float": [vector]}}, allow_nan=True)
        )
    )
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ProviderInvalidResponseError),
    ):
        await client.embed(["public synthetic input"])


@pytest.mark.asyncio
async def test_rerank_restores_candidate_order_and_rejects_duplicate_indices():
    client = gateway(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]
            },
        )
    )
    with inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"):
        assert await client.rerank("public query", ["first", "second"]) == [0.1, 0.9]
    client = gateway(
        lambda request: httpx.Response(
            200,
            json={
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]
            },
        )
    )
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ProviderInvalidResponseError),
    ):
        await client.rerank("public query", ["first", "second"])


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 429, 503])
async def test_provider_errors_are_bounded_and_do_not_leak_the_response(status):
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(status, text="PRIVATE-RESPONSE-SENTINEL")

    client = gateway(response)
    expected = {400: ProviderInvalidResponseError, 429: ProviderQuotaExceededError}.get(status, ProviderError)
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(expected) as error,
    ):
        await client.embed(["public synthetic input"])
    assert "PRIVATE-RESPONSE" not in str(error.value)
    assert len(requests) == 1  # No implicit retry or paid-provider fallback.


@pytest.mark.asyncio
async def test_context_isolation_and_token_limit_completion():
    client = gateway(
        lambda request: httpx.Response(
            200,
            json={
                "finish_reason": "MAX_TOKENS",
                "message": {"content": [{"type": "text", "text": "partial"}]},
            },
        )
    )

    async def allowed():
        with (
            inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
            pytest.raises(ProviderInvalidResponseError),
        ):
            await client.chat([{"role": "user", "content": "public fixture"}])

    async def denied():
        with pytest.raises(ProviderNotApprovedError):
            await client.embed(["public fixture without an authorized scope"])

    await asyncio.gather(allowed(), denied())


@pytest.mark.asyncio
async def test_non_png_image_cannot_be_mislabeled_or_sent_to_provider():
    client = gateway(lambda request: pytest.fail("must not send an invalid image"))
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ValueError, match="PNG"),
    ):
        await client.inspect_image(b"%PDF synthetic placeholder", "Read visible text")
    client.budget.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_production_scope_requires_a_postgres_authorization_validator(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    client = gateway(lambda request: pytest.fail("must not transmit content"))
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ProviderNotApprovedError, match="PostgreSQL authorization"),
    ):
        await client.embed(["PUBLIC SYNTHETIC FIXTURE"])
    client.budget.reserve.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_scope_is_revalidated_before_quota_or_transmission():
    client = gateway(lambda request: pytest.fail("must not transmit content"))
    validator = AsyncMock(side_effect=ProviderNotApprovedError("Synthetic scope revoked"))
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture", validator=validator),
        pytest.raises(ProviderNotApprovedError),
    ):
        await client.embed(["PUBLIC SYNTHETIC FIXTURE"])
    validator.assert_awaited_once()
    client.budget.reserve.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,code', [(httpx.ReadTimeout, 'provider_timeout'), (httpx.ConnectError, 'provider_unavailable')])
async def test_timeout_and_network_failures_remain_distinct_from_evidence_absence(failure, code):
    attempts = []
    def response(request):
        attempts.append(request)
        raise failure('synthetic private diagnostic')
    client = gateway(response)
    with (inference_scope(DataClassification.SYNTHETIC, 'fixture', 'fixture'), pytest.raises(ProviderError) as caught):
        await client.embed(['PUBLIC SYNTHETIC'])
    assert caught.value.code == code
    assert 'private diagnostic' not in str(caught.value)
    assert len(attempts) == 1
