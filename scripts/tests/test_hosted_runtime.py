import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from src.config import settings
from src.inference.chat_adapter import CohereChatAdapter
from src.inference.cohere_gateway import CohereGateway
from src.inference.policy import (
    DataClassification,
    ProviderError,
    ProviderNotApprovedError,
    ProviderQuotaExceededError,
    inference_scope,
)
from src.inference.sync_adapter import CohereSyncAdapter, PostgresLexicalAdapter
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import DocumentExtractor
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.retrieval.reranker import CrossEncoderReranker

KEY = "synthetic-hosted-adapter-fixture-key"


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setattr(settings, "COHERE_TRIAL_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    calls = []

    def response(request):
        payload = json.loads(request.content)
        calls.append((request.url.path, payload))
        if request.url.path.endswith("embed"):
            return httpx.Response(200, json={"embeddings": {"float": [[1.0] + [0.0] * 383 for _ in payload["texts"]]}})
        if request.url.path.endswith("rerank"):
            return httpx.Response(200, json={"results": [
                {"index": i, "relevance_score": 0.9 - i * 0.1} for i in range(len(payload["documents"]))
            ]})
        return httpx.Response(200, json={
            "finish_reason": "COMPLETE", "message": {"content": [{"type": "text", "text": "PUBLIC COMPLETE FIXTURE [1]"}]},
        })

    gateway = CohereGateway(KEY, transport=httpx.MockTransport(response), budget=SimpleNamespace(reserve=AsyncMock()))
    return CohereSyncAdapter(gateway), calls


@pytest.mark.asyncio
async def test_threaded_dense_adapter_preserves_scope_and_distinguishes_query_inputs(adapter):
    bridge, calls = adapter
    embedder = DenseEmbedder(model=bridge)
    with inference_scope(DataClassification.SYNTHETIC, "fixture-client", "fixture-tenant"):
        documents = await asyncio.to_thread(embedder.embed_texts, ["PUBLIC FIXTURE"])
        query = await asyncio.to_thread(embedder.embed_query, "PUBLIC FIXTURE")
    assert len(documents[0]) == len(query[0]) == 384
    assert [payload["input_type"] for _, payload in calls] == ["search_document", "search_query"]
    assert {payload["model"] for _, payload in calls} == {"embed-english-light-v3.0"}


@pytest.mark.asyncio
async def test_adapter_denies_missing_scope_and_main_loop_blocking(adapter):
    bridge, calls = adapter
    with pytest.raises(ProviderNotApprovedError):
        await asyncio.to_thread(bridge.embed, ["PUBLIC FIXTURE"])
    with (
        inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"),
        pytest.raises(ProviderError, match="worker thread"),
    ):
        bridge.embed(["PUBLIC FIXTURE"])
    assert calls == []


@pytest.mark.asyncio
async def test_rerank_provider_failures_cannot_be_disguised_as_successful_rrf_fallback():
    model = SimpleNamespace(rerank=lambda *args: (_ for _ in ()).throw(ProviderQuotaExceededError("Trial quota reached")))
    reranker = CrossEncoderReranker(model=model)
    candidates = [SimpleNamespace(content="PUBLIC FIXTURE", score=0.1)]
    with pytest.raises(ProviderQuotaExceededError):
        await asyncio.to_thread(reranker.rerank, "PUBLIC QUERY", candidates)


@pytest.mark.asyncio
async def test_generation_adapter_buffers_completion_and_uses_server_system_policy(adapter):
    bridge, calls = adapter
    chat = CohereChatAdapter(gateway=bridge.gateway)
    with inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"):
        events = [event async for event in chat.chat_stream(
            message="PUBLIC SOURCE FIXTURE", preamble="SERVER GROUNDING POLICY",
        )]
    assert len(events) == 1 and events[0].text == "PUBLIC COMPLETE FIXTURE [1]"
    assert calls[0][1]["messages"][0] == {"role": "system", "content": "SERVER GROUNDING POLICY"}


@pytest.mark.parametrize("method", ["_run_ocr", "_generate_description"])
def test_pdf_cannot_silently_index_after_provider_eligibility_or_quota_failure(method):
    def denied(*args):
        raise ProviderNotApprovedError("Unapproved fixture scope")
    extractor = DocumentExtractor(
        ocr_processor=SimpleNamespace(extract_text=denied),
        image_captioner=SimpleNamespace(describe=denied),
    )
    with pytest.raises(ProviderNotApprovedError):
        getattr(extractor, method)(b"synthetic-image-placeholder", 1)


def test_lexical_adapter_is_deterministic_and_uses_no_model():
    adapter = PostgresLexicalAdapter()
    assert adapter.embed(["Policy 42 policy"]) == adapter.embed(["Policy 42 policy"])
    assert adapter.query_embed("Policy 42")[0].indices
    assert adapter.embed(["   "])[0].indices == []


@pytest.mark.asyncio
async def test_provider_error_code_survives_the_complete_retriever_boundary():
    hybrid = SimpleNamespace(search=AsyncMock(side_effect=ProviderQuotaExceededError("Trial quota reached")))
    retriever = RerankedHybridRetriever(hybrid_retriever=hybrid)
    with pytest.raises(ProviderQuotaExceededError):
        await retriever.search("PUBLIC FIXTURE")


@pytest.mark.asyncio
async def test_gateway_concurrency_is_shared_across_instances(monkeypatch):
    monkeypatch.setattr(settings, "COHERE_TRIAL_KEY_SHA256", hashlib.sha256(KEY.encode()).hexdigest())
    active = peak = 0

    async def response(request):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"embeddings": {"float": [[1.0] + [0.0] * 383]}})

    budget = SimpleNamespace(reserve=AsyncMock())
    clients = [CohereGateway(KEY, transport=httpx.MockTransport(response), budget=budget) for _ in range(8)]
    with inference_scope(DataClassification.SYNTHETIC, "fixture", "fixture"):
        await asyncio.gather(*(client.embed(["PUBLIC FIXTURE"]) for client in clients))
    assert peak == 2 and budget.reserve.await_count == 8
