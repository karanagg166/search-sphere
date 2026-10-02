import json
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.processing.models.search import RerankedSearchResult
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import RewriteResult
from src.services.answer_generator import (
    AnswerGenerator,
    BaseAnswerProvider,
    get_answer_generator,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever
from src.storage.object_storage import LocalStorage, get_object_storage


class MockStreamingAnswerProvider(BaseAnswerProvider):
    """Deterministic mock provider yielding tokens for testing streaming responses."""

    def __init__(self, response_text: str = "Search Sphere uses hybrid search [1]."):
        self.response_text = response_text

    async def generate(self, query, context_chunks, conversation_context=None) -> str:
        return self.response_text

    async def generate_stream(
        self, query, context_chunks, conversation_context=None
    ) -> AsyncIterator[str]:
        tokens = ["Search ", "Sphere ", "uses ", "hybrid ", "search ", "[1]."]
        for token in tokens:
            yield token


def parse_sse_stream(raw_text: str) -> list[tuple[str, dict]]:
    """Parse raw SSE body into (event_name, data_dict) tuples."""
    events = []
    lines = raw_text.strip().split("\n")
    cur_event = None
    cur_data = []

    for line in lines:
        line = line.strip()
        if line.startswith("event:"):
            cur_event = line.replace("event:", "").strip()
        elif line.startswith("data:"):
            cur_data.append(line.replace("data:", "").strip())
        elif line == "":
            if cur_event and cur_data:
                payload_str = " ".join(cur_data)
                try:
                    payload = json.loads(payload_str)
                except Exception:
                    payload = {"raw": payload_str}
                events.append((cur_event, payload))
            cur_event = None
            cur_data = []

    if cur_event and cur_data:
        payload_str = " ".join(cur_data)
        try:
            payload = json.loads(payload_str)
        except Exception:
            payload = {"raw": payload_str}
        events.append((cur_event, payload))

    return events


async def create_test_user(
    client: AsyncClient, name: str
) -> tuple[str, str, dict[str, str]]:
    """Helper creating a test user and returning (user_id, token, headers)."""
    email = f"{name.lower().replace(' ', '_')}_{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/auth/signup",
        json={"name": name, "email": email, "password": "Password123!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return user_id, token, {"Authorization": f"Bearer {token}"}


# ==============================================================================
# Complete End-to-End Main Lifecycle Test
# ==============================================================================


@pytest.mark.asyncio
async def test_complete_e2e_rag_conversation_and_streaming_lifecycle(tmp_path):
    """
    Validates the entire end-to-end user journey:
    1. Signup and Login authentication
    2. Document upload and persistence (LocalStorage mocked, metadata in DB)
    3. Hybrid search with query rewrite and cross-encoder rerank
    4. Grounded RAG answer generation with verified sources
    5. Persistent conversation thread creation
    6. Multi-turn Q&A with conversational context memory
    7. Server-Sent Events (SSE) streaming answer delivery
    8. Feedback submission on assistant messages
    """
    storage = LocalStorage(base_dir=str(tmp_path), bucket="e2e-documents")
    app.dependency_overrides[get_object_storage] = lambda: storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Step 1: Authentication (Signup & verify login profile)
        user_id, token, headers = await create_test_user(client, "E2E Explorer")
        me_resp = await client.get("/auth/me", headers=headers)
        assert me_resp.status_code == 200
        assert me_resp.json()["id"] == user_id

        # Step 2: Document Flow (Upload, Metadata persistence, Task queueing)
        sample_pdf_bytes = (
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"
        )
        upload_resp = await client.post(
            "/documents",
            files={"file": ("handbook.pdf", sample_pdf_bytes, "application/pdf")},
            headers=headers,
        )
        assert upload_resp.status_code == 201
        doc_data = upload_resp.json()
        doc_id = doc_data["id"]
        assert doc_data["filename"] == "handbook.pdf"
        assert doc_data["user_id"] == user_id

        # Step 3: Mock Retrieval & Rewriting dependencies for controlled search/RAG
        mock_chunk = RerankedSearchResult(
            point_id="pt-101",
            document_id=doc_id,
            chunk_index=0,
            content="Search Sphere integrates dense and sparse BM25 retrieval via Reciprocal Rank Fusion.",
            token_count=14,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=9.45,
            rrf_score=0.033,
            score=9.45,
            rank=1,
        )
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search = AsyncMock(return_value=[mock_chunk])

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite = AsyncMock(
            return_value=RewriteResult(
                original_query="how does retrieval work?",
                retrieval_query="how does hybrid retrieval work?",
                rewritten=True,
            )
        )

        mock_provider = MockStreamingAnswerProvider(
            response_text="Search Sphere uses hybrid search with Reciprocal Rank Fusion [1]."
        )
        answer_gen = AnswerGenerator(provider=mock_provider)

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: answer_gen

        try:
            # Step 4: Search Flow (Query -> Rewrite -> Hybrid Retrieval -> RRF -> Cross-Encoder)
            search_resp = await client.post(
                "/search",
                json={"query": "how does retrieval work?", "document_id": doc_id},
                headers=headers,
            )
            assert search_resp.status_code == 200
            s_data = search_resp.json()
            assert s_data["rewritten"] is True
            assert len(s_data["results"]) == 1
            assert s_data["results"][0]["document_id"] == doc_id
            assert s_data["results"][0]["rerank_score"] == 9.45

            # Step 5: Grounded RAG Answer Flow (Direct Endpoint)
            answer_resp = await client.post(
                "/answer",
                json={"query": "how does retrieval work?", "document_id": doc_id},
                headers=headers,
            )
            assert answer_resp.status_code == 200
            a_data = answer_resp.json()
            assert "Search Sphere uses hybrid search" in a_data["answer"]
            assert len(a_data["sources"]) == 1
            assert a_data["sources"][0]["source_id"] == 1
            assert a_data["sources"][0]["document_id"] == doc_id

            # Step 6: Conversation Flow (Create thread -> Ask first question -> Verify persistence)
            conv_resp = await client.post(
                "/conversations",
                json={"title": "Architecture Discussion"},
                headers=headers,
            )
            assert conv_resp.status_code == 201
            conv_id = conv_resp.json()["id"]

            # Ask first question in conversation
            ask_resp_1 = await client.post(
                f"/conversations/{conv_id}/answer",
                json={"query": "What search algorithms are used?", "document_id": doc_id},
                headers=headers,
            )
            assert ask_resp_1.status_code == 200
            ans_data_1 = ask_resp_1.json()
            msg_id_1 = ans_data_1["message"]["id"]
            assert ans_data_1["conversation_id"] == conv_id

            # Verify conversation contains both user message and assistant response
            thread_resp_1 = await client.get(
                f"/conversations/{conv_id}", headers=headers
            )
            assert thread_resp_1.status_code == 200
            t_data_1 = thread_resp_1.json()
            assert len(t_data_1["messages"]) == 2
            assert t_data_1["messages"][0]["role"] == "user"
            assert t_data_1["messages"][1]["role"] == "assistant"
            assert t_data_1["messages"][1]["id"] == msg_id_1

            # Step 7: Follow-up question with conversation history
            ask_resp_2 = await client.post(
                f"/conversations/{conv_id}/answer",
                json={"query": "Can you explain that in more detail?"},
                headers=headers,
            )
            assert ask_resp_2.status_code == 200

            # Reload full conversation thread to verify continuous history
            thread_resp_2 = await client.get(
                f"/conversations/{conv_id}", headers=headers
            )
            assert thread_resp_2.status_code == 200
            t_data_2 = thread_resp_2.json()
            assert len(t_data_2["messages"]) == 4

            # Step 8: Streaming SSE Flow within Conversation
            stream_resp = await client.post(
                f"/conversations/{conv_id}/answer/stream",
                json={"query": "Stream me an explanation", "document_id": doc_id},
                headers=headers,
            )
            assert stream_resp.status_code == 200
            assert "text/event-stream" in stream_resp.headers["content-type"]

            events = parse_sse_stream(stream_resp.text)
            event_types = [e[0] for e in events]
            assert "metadata" in event_types
            assert "source" in event_types
            assert "token" in event_types
            assert "done" in event_types

            # Verify the done event contains message ID
            done_payload = next(e[1] for e in events if e[0] == "done")
            stream_msg_id = done_payload["message_id"]

            # Reload conversation: exactly 1 new assistant message added from streaming
            thread_resp_3 = await client.get(
                f"/conversations/{conv_id}", headers=headers
            )
            t_data_3 = thread_resp_3.json()
            assert len(t_data_3["messages"]) == 6
            assert t_data_3["messages"][-1]["id"] == stream_msg_id

            # Step 9: Feedback Submission
            fb_resp = await client.post(
                f"/conversations/{conv_id}/messages/{stream_msg_id}/feedback",
                json={"rating": 1, "comment": "Excellent streaming response and citations!"},
                headers=headers,
            )
            assert fb_resp.status_code == 201
            fb_data = fb_resp.json()
            assert fb_data["rating"] == 1
            assert fb_data["message_id"] == stream_msg_id

        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)
            app.dependency_overrides.pop(get_object_storage, None)


# ==============================================================================
# Strict Tenant Isolation Test
# ==============================================================================


@pytest.mark.asyncio
async def test_strict_multi_tenant_isolation(tmp_path):
    """
    Enforces absolute tenant boundaries between User A and User B:
    - User A CANNOT retrieve User B's documents (404)
    - User A CANNOT delete User B's documents (404)
    - User A CANNOT search User B's document directly (404)
    - User A general search NEVER includes User B's chunks (returns 0 chunks)
    - User A CANNOT access User B's conversations (404)
    - User A CANNOT send messages or ask in User B's conversations (404)
    - User A CANNOT submit feedback for User B's private messages (404)
    - User A CANNOT rename or delete User B's conversation (404)
    """
    storage = LocalStorage(base_dir=str(tmp_path), bucket="tenant-test")
    app.dependency_overrides[get_object_storage] = lambda: storage

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Provision User A and User B
        _, _, headers_a = await create_test_user(client, "User Alpha")
        _, _, headers_b = await create_test_user(client, "User Beta")

        # 2. User B uploads private document
        pdf_content = b"%PDF-1.4\nBeta confidential doc\n%%EOF"
        upload_b = await client.post(
            "/documents",
            files={"file": ("beta_secret.pdf", pdf_content, "application/pdf")},
            headers=headers_b,
        )
        assert upload_b.status_code == 201
        doc_b_id = upload_b.json()["id"]

        # 3. User B creates conversation with an assistant message
        conv_b_resp = await client.post(
            "/conversations",
            json={"title": "Beta Secret Project"},
            headers=headers_b,
        )
        assert conv_b_resp.status_code == 201
        conv_b_id = conv_b_resp.json()["id"]

        mock_provider = MockStreamingAnswerProvider("Confidential response for Beta.")
        gen = AnswerGenerator(provider=mock_provider)
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search = AsyncMock(return_value=[])
        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite = AsyncMock(
            return_value=RewriteResult(
                original_query="secret question",
                retrieval_query="secret question",
                rewritten=False,
            )
        )

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: gen

        try:
            ans_b = await client.post(
                f"/conversations/{conv_b_id}/answer",
                json={"query": "What is our secret formula?"},
                headers=headers_b,
            )
            assert ans_b.status_code == 200
            msg_b_id = ans_b.json()["message"]["id"]

            # ==============================================================
            # Tenant Isolation Verification: User A attempts access to User B
            # ==============================================================

            # A) User A cannot view User B's document
            get_doc_resp = await client.get(f"/documents/{doc_b_id}", headers=headers_a)
            assert get_doc_resp.status_code == 404

            # B) User A cannot delete User B's document
            del_doc_resp = await client.delete(
                f"/documents/{doc_b_id}", headers=headers_a
            )
            assert del_doc_resp.status_code == 404

            # C) User A cannot execute search scoped to User B's document
            search_scoped_resp = await client.post(
                "/search",
                json={"query": "secret formula", "document_id": doc_b_id},
                headers=headers_a,
            )
            assert search_scoped_resp.status_code == 404

            # D) User A general search returns empty results immediately without querying Qdrant
            search_global_resp = await client.post(
                "/search",
                json={"query": "secret formula"},
                headers=headers_a,
            )
            assert search_global_resp.status_code == 200
            assert search_global_resp.json()["total"] == 0
            assert search_global_resp.json()["results"] == []

            # E) User A cannot view User B's conversation
            get_conv_resp = await client.get(
                f"/conversations/{conv_b_id}", headers=headers_a
            )
            assert get_conv_resp.status_code == 404

            # F) User A cannot ask questions in User B's conversation
            ask_in_conv_resp = await client.post(
                f"/conversations/{conv_b_id}/answer",
                json={"query": "Intruder query"},
                headers=headers_a,
            )
            assert ask_in_conv_resp.status_code == 404

            # G) User A cannot stream in User B's conversation
            stream_in_conv_resp = await client.post(
                f"/conversations/{conv_b_id}/answer/stream",
                json={"query": "Intruder stream"},
                headers=headers_a,
            )
            assert stream_in_conv_resp.status_code == 200
            # SSE yields an error event for unauthorized conversation
            assert "event: error" in stream_in_conv_resp.text
            assert "Conversation not found" in stream_in_conv_resp.text

            # H) User A cannot submit feedback for User B's conversation message
            feedback_resp = await client.post(
                f"/conversations/{conv_b_id}/messages/{msg_b_id}/feedback",
                json={"rating": -1, "comment": "Malicious rating attempt"},
                headers=headers_a,
            )
            assert feedback_resp.status_code == 404

            # I) User A cannot rename User B's conversation
            rename_resp = await client.put(
                f"/conversations/{conv_b_id}",
                json={"title": "Hacked Title"},
                headers=headers_a,
            )
            assert rename_resp.status_code == 404

            # J) User A cannot delete User B's conversation
            delete_conv_resp = await client.delete(
                f"/conversations/{conv_b_id}", headers=headers_a
            )
            assert delete_conv_resp.status_code == 404

        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)
            app.dependency_overrides.pop(get_object_storage, None)
