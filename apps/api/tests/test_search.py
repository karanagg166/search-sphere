import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.db import AsyncSessionLocal
from src.main import app
from src.models.document import Document
from src.processing.models.search import RerankedSearchResult
from src.retrieval.reranked_retriever import (
    RerankedHybridRetriever,
    RerankedQueryValidationError,
    RerankedRetrievalError,
)
from src.schemas.search import RewriteResult
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever


def make_reranked_result(
    point_id: str = "point-1",
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Redis supports multiple cache eviction policies.",
    token_count: int = 7,
    start_page: int = 8,
    end_page: int = 9,
    page_numbers: list[int] | None = None,
    block_types: list[str] | None = None,
    rerank_score: float = 8.42,
    rrf_score: float | None = 0.0327,
    rank: int = 1,
) -> RerankedSearchResult:
    """Helper creating sample RerankedSearchResult fixture."""
    return RerankedSearchResult(
        point_id=point_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        token_count=token_count,
        start_page=start_page,
        end_page=end_page,
        page_numbers=page_numbers or [8, 9],
        block_types=block_types or ["text"],
        rerank_score=rerank_score,
        rrf_score=rrf_score,
        score=rerank_score,
        rank=rank,
    )


async def create_user_with_token(
    client: AsyncClient, prefix: str = "user"
) -> tuple[str, str, dict[str, str]]:
    """Helper creating a test user and returning (user_id, token, auth_headers)."""
    email = f"{prefix}_{uuid.uuid4().hex[:8]}@testsphere.io"
    resp = await client.post(
        "/auth/signup",
        json={"name": "Test User", "email": email, "password": "Password123!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return user_id, token, {"Authorization": f"Bearer {token}"}


async def insert_test_document(user_id: str, filename: str = "doc.pdf") -> Document:
    """Directly persist a document row for testing user-document ownership."""
    doc_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        doc = Document(
            id=doc_id,
            user_id=user_id,
            filename=filename,
            storage_key=f"documents/{user_id}/{doc_id}.pdf",
            file_url=f"http://test/storage/{doc_id}.pdf",
            file_size=1024,
            mime_type="application/pdf",
            status="indexed",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


# ==============================================================================
# 1. Authentication Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_unauthenticated_search_rejected() -> None:
    """POST /search without token must fail with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/search", json={"query": "how does caching work?"})
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_token_search_rejected() -> None:
    """POST /search with malformed token must fail with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/search",
            json={"query": "test query"},
            headers={"Authorization": "Bearer completely-invalid-token"},
        )
        assert resp.status_code == 401


# ==============================================================================
# 2. Request Validation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_empty_query_rejected() -> None:
    """Empty query must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        resp = await client.post("/search", json={"query": ""}, headers=headers)
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_whitespace_query_rejected() -> None:
    """Whitespace-only query must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        resp = await client.post(
            "/search", json={"query": "   \n\t  "}, headers=headers
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_invalid_top_k_rejected() -> None:
    """top_k <= 0 or > 100 must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)

        # Non-positive
        resp1 = await client.post(
            "/search",
            json={"query": "valid query", "top_k": 0},
            headers=headers,
        )
        assert resp1.status_code == 422

        # Negative
        resp2 = await client.post(
            "/search",
            json={"query": "valid query", "top_k": -5},
            headers=headers,
        )
        assert resp2.status_code == 422

        # Exceeds max limit (100)
        resp3 = await client.post(
            "/search",
            json={"query": "valid query", "top_k": 105},
            headers=headers,
        )
        assert resp3.status_code == 422


@pytest.mark.asyncio
async def test_search_invalid_candidate_k_rejected() -> None:
    """candidate_k <= 0 must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        resp = await client.post(
            "/search",
            json={"query": "valid query", "top_k": 5, "candidate_k": 0},
            headers=headers,
        )
        assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_candidate_k_less_than_top_k_rejected() -> None:
    """candidate_k < top_k must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        resp = await client.post(
            "/search",
            json={"query": "valid query", "top_k": 10, "candidate_k": 5},
            headers=headers,
        )
        assert resp.status_code == 422
        assert "cannot be less than top_k" in str(resp.json())


@pytest.mark.asyncio
async def test_search_empty_document_id_rejected() -> None:
    """document_id if supplied as empty or whitespace must be rejected with 422."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        resp = await client.post(
            "/search",
            json={"query": "valid query", "document_id": "   "},
            headers=headers,
        )
        assert resp.status_code == 422


# ==============================================================================
# 3. Search Behavior & Result Mapping Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_successful_reranked_search() -> None:
    """
    Valid authenticated search:
    - Calls retriever
    - Returns mapped SearchResponse
    - Preserves rank, rerank_score, and rrf_score
    - Formats all metadata fields correctly
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        doc = await insert_test_document(user_id=user_id, filename="redis.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        sample_results = [
            make_reranked_result(
                point_id="p-1",
                document_id=doc.id,
                chunk_index=4,
                content="Redis supports several eviction policies like LRU and LFU.",
                start_page=8,
                end_page=9,
                page_numbers=[8, 9],
                block_types=["text"],
                rerank_score=8.42,
                rrf_score=0.0327,
                rank=1,
            ),
            make_reranked_result(
                point_id="p-2",
                document_id=doc.id,
                chunk_index=2,
                content="Volatile-lru evicts keys with an expire set.",
                start_page=5,
                end_page=5,
                page_numbers=[5],
                block_types=["text"],
                rerank_score=6.15,
                rrf_score=0.0164,
                rank=2,
            ),
        ]
        mock_retriever.search.return_value = sample_results
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={
                    "query": "How does Redis eviction work?",
                    "top_k": 5,
                    "candidate_k": 20,
                    "document_id": doc.id,
                },
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["query"] == "How does Redis eviction work?"
            assert data["total"] == 2
            assert len(data["results"]) == 2

            first = data["results"][0]
            assert first["document_id"] == doc.id
            assert first["chunk_index"] == 4
            assert (
                first["content"]
                == "Redis supports several eviction policies like LRU and LFU."
            )
            assert first["start_page"] == 8
            assert first["end_page"] == 9
            assert first["page_numbers"] == [8, 9]
            assert first["block_types"] == ["text"]
            assert first["rerank_score"] == 8.42
            assert first["rrf_score"] == 0.0327
            assert first["rank"] == 1

            second = data["results"][1]
            assert second["rank"] == 2
            assert second["rerank_score"] == 6.15
            assert second["rrf_score"] == 0.0164

            # Verify retriever call arguments
            mock_retriever.search.assert_awaited_once_with(
                query="How does Redis eviction work?",
                top_k=5,
                candidate_k=20,
                document_id=doc.id,
                document_ids=None,
                score_threshold=None,
            )
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    """When retriever yields no chunks, returns empty results list with total=0."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        doc = await insert_test_document(user_id=user_id)

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.return_value = []
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "obscure non-existent topic", "document_id": doc.id},
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["query"] == "obscure non-existent topic"
            assert data["total"] == 0
            assert data["results"] == []
        finally:
            app.dependency_overrides.pop(get_retriever, None)


# ==============================================================================
# 4. Authorization & Tenant Isolation Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_scoped_to_other_user_document_rejected() -> None:
    """User A cannot search a document belonging to User B (must return 404)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create User A
        _, _, headers_a = await create_user_with_token(client, prefix="usera")

        # Create User B and User B's document
        user_b_id, _, _ = await create_user_with_token(client, prefix="userb")
        doc_b = await insert_test_document(user_id=user_b_id, filename="private_b.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "confidential details", "document_id": doc_b.id},
                headers=headers_a,
            )
            assert resp.status_code == 404
            assert "Document not found" in resp.json()["detail"]
            # Ensure retriever was NEVER called
            mock_retriever.search.assert_not_awaited()
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_search_nonexistent_document_rejected() -> None:
    """Searching for a non-existent document ID returns 404."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client)
        random_doc_id = str(uuid.uuid4())

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "test query", "document_id": random_doc_id},
                headers=headers,
            )
            assert resp.status_code == 404
            assert "Document not found" in resp.json()["detail"]
            mock_retriever.search.assert_not_awaited()
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_search_all_user_documents_with_zero_documents() -> None:
    """When a user has no documents uploaded, search returns 0 results immediately."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers = await create_user_with_token(client, prefix="nodocs")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "anything"},
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 0
            assert data["results"] == []
            mock_retriever.search.assert_not_awaited()
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_search_all_user_documents_scoped_to_user_only() -> None:
    """When document_id is omitted, search is scoped exclusively to user's documents."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # User A owns doc 1 and doc 2
        user_a_id, _, headers_a = await create_user_with_token(
            client, prefix="usera_multi"
        )
        doc_1 = await insert_test_document(user_id=user_a_id, filename="doc1.pdf")
        doc_2 = await insert_test_document(user_id=user_a_id, filename="doc2.pdf")

        # User B owns doc 3
        user_b_id, _, _ = await create_user_with_token(client, prefix="userb_other")
        await insert_test_document(user_id=user_b_id, filename="doc3.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.return_value = [
            make_reranked_result(document_id=doc_1.id, content="Doc 1 chunk"),
            make_reranked_result(document_id=doc_2.id, content="Doc 2 chunk"),
        ]
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "distributed systems"},
                headers=headers_a,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["total"] == 2

            # Check that retriever was called with ONLY user A's document IDs
            mock_retriever.search.assert_awaited_once()
            call_kwargs = mock_retriever.search.await_args.kwargs
            assert call_kwargs["document_id"] is None
            passed_doc_ids = set(call_kwargs["document_ids"])
            assert passed_doc_ids == {doc_1.id, doc_2.id}
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_defense_in_depth_leakage_filter() -> None:
    """If retriever somehow returns an unowned document chunk, it is filtered out."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_a_id, _, headers_a = await create_user_with_token(
            client, prefix="leak_test"
        )
        doc_a = await insert_test_document(user_id=user_a_id, filename="doc_a.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        # Rogue result from doc_b leaked by vector store
        rogue_result = make_reranked_result(
            document_id="unauthorized-doc-b", content="Secret leak"
        )
        valid_result = make_reranked_result(
            document_id=doc_a.id, content="Authorized content"
        )
        mock_retriever.search.return_value = [rogue_result, valid_result]
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "test query"},
                headers=headers_a,
            )
            assert resp.status_code == 200
            data = resp.json()
            # Only the authorized chunk survives
            assert data["total"] == 1
            assert data["results"][0]["document_id"] == doc_a.id
            assert data["results"][0]["content"] == "Authorized content"
        finally:
            app.dependency_overrides.pop(get_retriever, None)


# ==============================================================================
# 5. Failure Handling & Error Sanitization Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_retriever_query_validation_error_mapped_to_400() -> None:
    """RerankedQueryValidationError from retriever maps to 400 Bad Request."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        await insert_test_document(user_id=user_id)

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.side_effect = RerankedQueryValidationError(
            "Query parameter invalid."
        )
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "some query"},
                headers=headers,
            )
            assert resp.status_code == 400
            assert "Query parameter invalid." in resp.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_retrieval_error_mapped_to_500_without_leaking_internals() -> None:
    """Internal retrieval errors map to 500 without leaking credentials or tracebacks."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        await insert_test_document(user_id=user_id)

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.side_effect = RerankedRetrievalError(
            "Connection failed: http://user:secret-api-key@qdrant-cluster:6333"
        )
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "test query"},
                headers=headers,
            )
            assert resp.status_code == 500
            detail = resp.json()["detail"]
            assert detail == "Search service encountered an internal retrieval error."
            # Confirm credentials/URLs are NOT in the response
            assert "secret-api-key" not in detail
            assert "qdrant-cluster" not in detail
        finally:
            app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_unexpected_exception_mapped_to_500_without_leaks() -> None:
    """Unexpected exceptions map to safe 500 response."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        await insert_test_document(user_id=user_id)

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.side_effect = RuntimeError(
            "Critical unexpected bug in sentence_transformers"
        )
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={"query": "test query"},
                headers=headers,
            )
            assert resp.status_code == 500
            assert (
                resp.json()["detail"] == "An unexpected error occurred during search."
            )
            assert "sentence_transformers" not in resp.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_retriever, None)


# ==============================================================================
# 9. Query Rewriting Integration Tests
# ==============================================================================


@pytest.mark.asyncio
async def test_search_with_query_rewriting_context() -> None:
    """
    When search query has conversation context and is rewritten:
    - original query is preserved in response ('query')
    - rewritten standalone query is exposed ('retrieval_query')
    - rewritten flag is True
    - retriever is invoked with the rewritten query
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        doc = await insert_test_document(user_id=user_id, filename="redis.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.return_value = [
            make_reranked_result(
                document_id=doc.id, content="Allkeys-lru evicts keys based on LRU."
            )
        ]

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite.return_value = RewriteResult(
            original_query="which one is better?",
            retrieval_query="Which Redis eviction policy is best for caching?",
            rewritten=True,
            reason="Resolved reference using conversation history",
        )

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter

        try:
            resp = await client.post(
                "/search",
                json={
                    "query": "which one is better?",
                    "conversation_context": [
                        {"role": "user", "content": "Explain Redis eviction policies"}
                    ],
                    "document_id": doc.id,
                },
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()

            # Verify response schema fields
            assert data["query"] == "which one is better?"
            assert (
                data["retrieval_query"]
                == "Which Redis eviction policy is best for caching?"
            )
            assert data["rewritten"] is True
            assert data["total"] == 1

            # Verify retriever was passed the rewritten standalone query
            mock_retriever.search.assert_awaited_once_with(
                query="Which Redis eviction policy is best for caching?",
                top_k=5,
                candidate_k=20,
                document_id=doc.id,
                document_ids=None,
                score_threshold=None,
            )
        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)


@pytest.mark.asyncio
async def test_search_with_query_rewriting_failure_falls_back_gracefully() -> None:
    """
    If query rewriting encounters an error/fallback, the search endpoint
    proceeds uninterrupted using the original user query.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user_with_token(client)
        doc = await insert_test_document(user_id=user_id, filename="redis.pdf")

        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search.return_value = [
            make_reranked_result(document_id=doc.id, content="Fallback search result.")
        ]

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite.return_value = RewriteResult(
            original_query="how does that work?",
            retrieval_query="how does that work?",
            rewritten=False,
            reason="Cohere timed out",
        )

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter

        try:
            resp = await client.post(
                "/search",
                json={
                    "query": "how does that work?",
                    "conversation_context": [
                        {"role": "user", "content": "Explain Redis cache"}
                    ],
                    "document_id": doc.id,
                },
                headers=headers,
            )
            assert resp.status_code == 200
            data = resp.json()

            assert data["query"] == "how does that work?"
            assert data["retrieval_query"] == "how does that work?"
            assert data["rewritten"] is False

            # Retriever received original query
            mock_retriever.search.assert_awaited_once_with(
                query="how does that work?",
                top_k=5,
                candidate_k=20,
                document_id=doc.id,
                document_ids=None,
                score_threshold=None,
            )
        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)


@pytest.mark.asyncio
async def test_search_forwards_score_threshold_parameter() -> None:
    """Verify that score_threshold in search request is passed to retriever."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, token, headers = await create_user_with_token(client, prefix="score_th")
        doc = await insert_test_document(user_id=user_id, filename="redis.pdf")

        mock_retriever = AsyncMock()
        mock_retriever.search.return_value = [
            make_reranked_result(document_id=doc.id, rerank_score=2.5)
        ]
        app.dependency_overrides[get_retriever] = lambda: mock_retriever

        try:
            resp = await client.post(
                "/search",
                json={
                    "query": "redis caching",
                    "score_threshold": 1.0,
                    "document_id": doc.id,
                },
                headers=headers,
            )
            assert resp.status_code == 200
            mock_retriever.search.assert_awaited_once_with(
                query="redis caching",
                top_k=5,
                candidate_k=20,
                document_id=doc.id,
                document_ids=None,
                score_threshold=1.0,
            )
        finally:
            app.dependency_overrides.pop(get_retriever, None)


def test_cross_encoder_score_threshold_and_deduplication() -> None:
    """Verify that CrossEncoderReranker prunes below threshold and deduplicates overlapping chunks."""
    from unittest.mock import MagicMock

    from src.retrieval.reranker import CrossEncoderReranker

    class DummyCandidate:
        def __init__(self, doc_id: str, chunk_idx: int, content: str, score: float = 0.0):
            self.document_id = doc_id
            self.chunk_index = chunk_idx
            self.content = content
            self.score = score

    candidates = [
        DummyCandidate("doc-1", 0, "Harsh Mishra Contact Info Email LinkedIn"),
        DummyCandidate("doc-1", 2, "Education IIITDM Jabalpur B.Tech Experience at Clink AI"),
        DummyCandidate("doc-1", 1, "Education IIITDM Jabalpur B.Tech"),  # Overlaps chunk 2!
        DummyCandidate("doc-1", 3, "Irrelevant project Quick Clinic details"),
    ]

    mock_model = MagicMock()
    # Mock scores: cand 0 -> 2.0, cand 1 -> 1.5, cand 2 -> 1.4, cand 3 -> -12.0
    mock_model.predict.return_value = [2.0, 1.5, 1.4, -12.0]
    mock_model.rerank.return_value = [2.0, 1.5, 1.4, -12.0]

    reranker = CrossEncoderReranker(model=mock_model)

    # With score_threshold = 0.0, the -12.0 chunk is pruned; chunk 1 is pruned due to overlap with chunk 2
    results = reranker.rerank(
        query="what college harsh mishra ?",
        candidates=candidates,
        top_k=5,
        score_threshold=0.0,
        deduplicate=True,
    )

    # Should only return chunk 0 and chunk 2 (chunk 1 dropped due to redundancy, chunk 3 dropped due to score < 0.0)
    assert len(results) == 2
    assert results[0].chunk_index == 0
    assert results[0].rerank_score == 2.0
    assert results[1].chunk_index == 2
    assert results[1].rerank_score == 1.5

