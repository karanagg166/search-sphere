import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.db import AsyncSessionLocal
from src.main import app
from src.models.document import Document
from src.processing.models.search import RerankedSearchResult
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import RewriteResult
from src.services.answer_generator import (
    AnswerGenerator,
    get_answer_generator,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever


def make_sample_chunk(
    point_id: str = "point-1",
    document_id: str = "doc-1",
    chunk_index: int = 0,
    content: str = "Search Sphere uses persistent conversations for context-aware Q&A.",
    rerank_score: float = 8.5,
) -> RerankedSearchResult:
    return RerankedSearchResult(
        point_id=point_id,
        document_id=document_id,
        chunk_index=chunk_index,
        content=content,
        token_count=10,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=rerank_score,
        rrf_score=0.03,
        score=rerank_score,
        rank=1,
    )


async def create_user(client: AsyncClient, name: str = "User") -> tuple[str, str, dict[str, str]]:
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


async def insert_test_document(user_id: str, filename: str = "doc.pdf") -> Document:
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


@pytest.mark.asyncio
async def test_create_and_list_conversations():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers_a = await create_user(client, "User A")
        _, _, headers_b = await create_user(client, "User B")

        # Create conversation for User A
        resp = await client.post(
            "/conversations",
            json={"title": "Project Architecture"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Project Architecture"
        conv_a_id = data["id"]
        assert data["message_count"] == 0

        # Create another conversation for User A with default title
        resp2 = await client.post(
            "/conversations",
            json={},
            headers=headers_a,
        )
        assert resp2.status_code == 201
        assert resp2.json()["title"] == "New Conversation"

        # Create conversation for User B
        resp_b = await client.post(
            "/conversations",
            json={"title": "User B Thread"},
            headers=headers_b,
        )
        assert resp_b.status_code == 201
        conv_b_id = resp_b.json()["id"]

        # List conversations for User A
        list_a = await client.get("/conversations", headers=headers_a)
        assert list_a.status_code == 200
        a_items = list_a.json()
        a_ids = [c["id"] for c in a_items]
        assert conv_a_id in a_ids
        assert conv_b_id not in a_ids

        # List conversations for User B
        list_b = await client.get("/conversations", headers=headers_b)
        assert list_b.status_code == 200
        b_items = list_b.json()
        b_ids = [c["id"] for c in b_items]
        assert conv_b_id in b_ids
        assert conv_a_id not in b_ids


@pytest.mark.asyncio
async def test_get_conversation_tenant_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers_a = await create_user(client, "Owner")
        _, _, headers_b = await create_user(client, "Attacker")

        # Owner creates conversation
        resp = await client.post(
            "/conversations",
            json={"title": "Confidential Conversation"},
            headers=headers_a,
        )
        assert resp.status_code == 201
        conv_id = resp.json()["id"]

        # Owner can retrieve own conversation
        get_owner = await client.get(f"/conversations/{conv_id}", headers=headers_a)
        assert get_owner.status_code == 200
        data = get_owner.json()
        assert data["id"] == conv_id
        assert data["messages"] == []

        # Attacker cannot retrieve Owner's conversation (404)
        get_attacker = await client.get(f"/conversations/{conv_id}", headers=headers_b)
        assert get_attacker.status_code == 404

        # Nonexistent conversation id
        get_missing = await client.get(
            f"/conversations/{uuid.uuid4()}", headers=headers_a
        )
        assert get_missing.status_code == 404


@pytest.mark.asyncio
async def test_update_and_delete_conversation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        _, _, headers_a = await create_user(client, "Owner")
        _, _, headers_b = await create_user(client, "Other")

        resp = await client.post(
            "/conversations",
            json={"title": "Old Title"},
            headers=headers_a,
        )
        conv_id = resp.json()["id"]

        # Other user cannot update title
        resp_bad_update = await client.put(
            f"/conversations/{conv_id}",
            json={"title": "Hacked Title"},
            headers=headers_b,
        )
        assert resp_bad_update.status_code == 404

        # Owner updates title
        resp_update = await client.put(
            f"/conversations/{conv_id}",
            json={"title": "Updated Title"},
            headers=headers_a,
        )
        assert resp_update.status_code == 200
        assert resp_update.json()["title"] == "Updated Title"

        # Other user cannot delete
        resp_bad_del = await client.delete(
            f"/conversations/{conv_id}",
            headers=headers_b,
        )
        assert resp_bad_del.status_code == 404

        # Owner deletes
        resp_del = await client.delete(
            f"/conversations/{conv_id}",
            headers=headers_a,
        )
        assert resp_del.status_code == 204

        # Verify it no longer exists
        resp_verify = await client.get(f"/conversations/{conv_id}", headers=headers_a)
        assert resp_verify.status_code == 404


@pytest.mark.asyncio
async def test_conversation_answer_flow_and_context_history():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user(client, "ChatUser")
        doc = await insert_test_document(user_id=user_id, filename="knowledge.pdf")

        # Create new conversation
        conv_resp = await client.post("/conversations", json={}, headers=headers)
        assert conv_resp.status_code == 201
        conv_id = conv_resp.json()["id"]

        # Prepare mocks for retrieval, query rewriting, and answer generation
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_sample_chunk = make_sample_chunk(document_id=doc.id)
        mock_retriever.search = AsyncMock(return_value=[mock_sample_chunk])

        captured_contexts = []

        mock_rewriter = AsyncMock(spec=QueryRewriter)

        async def fake_rewrite(query, conversation_context=None):
            captured_contexts.append(conversation_context)
            if conversation_context and len(conversation_context) > 0:
                return RewriteResult(
                    original_query=query,
                    retrieval_query=f"reformulated {query}",
                    rewritten=True,
                    reason="Resolved reference from context",
                )
            return RewriteResult(
                original_query=query,
                retrieval_query=query,
                rewritten=False,
            )

        mock_rewriter.rewrite = AsyncMock(side_effect=fake_rewrite)

        mock_generator = AsyncMock(spec=AnswerGenerator)
        mock_generator.generate_answer = AsyncMock(
            return_value=("Search Sphere persists chat context [1].", [mock_sample_chunk])
        )

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: mock_generator

        try:
            # Turn 1: Initial Question
            turn1_resp = await client.post(
                f"/conversations/{conv_id}/answer",
                json={"query": "What is Search Sphere?"},
                headers=headers,
            )
            assert turn1_resp.status_code == 200
            t1_data = turn1_resp.json()
            assert t1_data["conversation_id"] == conv_id
            assert t1_data["query"] == "What is Search Sphere?"
            assert "Search Sphere persists chat context" in t1_data["answer"]
            assert len(t1_data["sources"]) == 1

            # Turn 1 had no prior conversation context
            assert len(captured_contexts) == 1
            assert captured_contexts[0] == [] or captured_contexts[0] is None

            # Verify conversation title was auto-updated from default "New Conversation"
            conv_detail = await client.get(f"/conversations/{conv_id}", headers=headers)
            assert conv_detail.status_code == 200
            detail_data = conv_detail.json()
            assert detail_data["title"] == "What is Search Sphere?"
            assert len(detail_data["messages"]) == 2
            assert detail_data["messages"][0]["role"] == "user"
            assert detail_data["messages"][0]["content"] == "What is Search Sphere?"
            assert detail_data["messages"][1]["role"] == "assistant"
            assert detail_data["messages"][1]["sources"] is not None

            # Turn 2: Follow-up Question
            mock_generator.generate_answer = AsyncMock(
                return_value=("It uses hybrid retrieval [1].", [mock_sample_chunk])
            )
            turn2_resp = await client.post(
                f"/conversations/{conv_id}/answer",
                json={"query": "How does it do that?"},
                headers=headers,
            )
            assert turn2_resp.status_code == 200
            t2_data = turn2_resp.json()
            assert t2_data["rewritten"] is True

            # Turn 2 must have passed the history from Turn 1 to the query rewriter!
            assert len(captured_contexts) == 2
            turn2_context = captured_contexts[1]
            assert turn2_context is not None
            assert len(turn2_context) == 2
            assert turn2_context[0].role == "user"
            assert turn2_context[0].content == "What is Search Sphere?"
            assert turn2_context[1].role == "assistant"
            assert "Search Sphere persists chat context" in turn2_context[1].content

            # Verify full history in conversation
            final_conv = await client.get(f"/conversations/{conv_id}", headers=headers)
            assert final_conv.status_code == 200
            assert len(final_conv.json()["messages"]) == 4

        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)


@pytest.mark.asyncio
async def test_unauthenticated_conversations():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/conversations")
        assert resp.status_code == 401

        resp = await client.post("/conversations", json={"title": "Test"})
        assert resp.status_code == 401

        resp = await client.get(f"/conversations/{uuid.uuid4()}")
        assert resp.status_code == 401
