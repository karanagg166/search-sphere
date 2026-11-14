"""Tests for RRF fusion, reranking, query rewriting, and HybridRagEngine."""

import json

import httpx
import pytest

from search_sphere_rag import (
    CohereEmbeddingProvider,
    CohereRerankProvider,
    ConversationTurn,
    HybridRagEngine,
    QueryIntentRoute,
    RetrievalCandidate,
    classify_query_intent,
    deduplicate_candidates,
    reciprocal_rank_fusion,
    requires_history,
    rewrite_query,
)


class _StubVectorRetriever:
    async def search_dense(self, query_vector, *, space, scope, limit):
        assert scope["patient_id"] == "pat_1"
        return [
            RetrievalCandidate(
                chunk_id="c1",
                document_id="d1",
                content="Hemoglobin A1c is 5.8%.",
                score=0.92,
                start_page=1,
            ),
            RetrievalCandidate(
                chunk_id="c2",
                document_id="d1",
                content="Fasting glucose is 95 mg/dL.",
                score=0.75,
                start_page=2,
            ),
        ][:limit]


class _StubLexicalRetriever:
    async def search_lexical(self, query, *, scope, limit):
        assert scope["patient_id"] == "pat_1"
        return [
            RetrievalCandidate(
                chunk_id="c2",
                document_id="d1",
                content="Fasting glucose is 95 mg/dL.",
                score=0.88,
                start_page=2,
            ),
            RetrievalCandidate(
                chunk_id="c3",
                document_id="d2",
                content="Vitamin D level is 32 ng/mL.",
                score=0.40,
                start_page=1,
            ),
        ][:limit]


def test_rrf_and_dedup() -> None:
    c1 = RetrievalCandidate(chunk_id="c1", document_id="d1", content="HbA1c 5.8 percent normal", score=0.9)
    c2 = RetrievalCandidate(chunk_id="c2", document_id="d1", content="HbA1c 5.8 percent normal", score=0.8)
    fused = reciprocal_rank_fusion([c1, c2], [c2], top_k=5)
    assert fused[0].candidate.chunk_id == "c2"
    deduped = deduplicate_candidates(fused, similarity_threshold=0.8)
    assert len(deduped) == 1


@pytest.mark.asyncio
async def test_hybrid_rag_engine_with_cohere_rerank_and_fallback() -> None:
    def embed_handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"embeddings": {"float": [[0.1] * 384 for _ in body["texts"]]}})

    def rerank_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [{"index": 0, "relevance_score": 0.99}, {"index": 1, "relevance_score": 0.65}]},
        )

    engine = HybridRagEngine(
        vector_retriever=_StubVectorRetriever(),
        lexical_retriever=_StubLexicalRetriever(),
        embedding_provider=CohereEmbeddingProvider(
            api_key="test-key",
            transport=httpx.MockTransport(embed_handler),
        ),
        reranker=CohereRerankProvider(
            api_key="test-key",
            transport=httpx.MockTransport(rerank_handler),
        ),
    )
    resp = await engine.retrieve("What is my fasting glucose?", scope={"patient_id": "pat_1"}, top_k=2)
    assert resp.strategy == "hybrid"
    assert resp.reranked is True
    assert resp.reranker_model == "rerank-v3.5"
    assert len(resp.results) == 2

    # Verify fallback when reranker fails
    failing_rerank = CohereRerankProvider(
        api_key="test-key",
        transport=httpx.MockTransport(lambda _: httpx.Response(503)),
    )
    engine_fallback = HybridRagEngine(
        vector_retriever=_StubVectorRetriever(),
        lexical_retriever=_StubLexicalRetriever(),
        reranker=failing_rerank,
    )
    resp2 = await engine_fallback.retrieve("fasting glucose", scope={"patient_id": "pat_1"}, top_k=2)
    assert resp2.strategy == "sparse"
    assert resp2.reranked is False
    assert resp2.reranker_model == "hybrid_rrf_fallback"
    assert len(resp2.results) == 2


@pytest.mark.asyncio
async def test_query_intent_history_and_rewrite() -> None:
    assert classify_query_intent("hello").route == QueryIntentRoute.DIRECT
    assert classify_query_intent("What is my document processing status?").route == QueryIntentRoute.TOOL_API
    assert requires_history("What about its reference range?") is True

    rewritten = await rewrite_query(
        "What about its reference range?",
        [ConversationTurn(role="USER", content="Check my HbA1c lab report")],
    )
    assert rewritten.was_rewritten is True
    assert "HbA1c" in rewritten.rewritten_query
