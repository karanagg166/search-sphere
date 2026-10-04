import json
import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from src.db import AsyncSessionLocal
from src.main import app
from src.models.document import Document
from src.processing.models.search import RerankedSearchResult
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.search import RewriteResult
from src.services.answer_generator import (
    AnswerGenerationError,
    AnswerGenerator,
    BaseAnswerProvider,
    get_answer_generator,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever


def parse_sse_events(raw_body: str) -> list[tuple[str, dict]]:
    """Parse raw Server-Sent Events text body into (event_type, json_data) tuples."""
    events = []
    current_event = None
    current_data = []

    for line in raw_body.strip().split("\n"):
        line = line.strip()
        if line.startswith("event:"):
            current_event = line.replace("event:", "").strip()
        elif line.startswith("data:"):
            current_data.append(line.replace("data:", "").strip())
        elif line == "":
            if current_event and current_data:
                data_str = " ".join(current_data)
                try:
                    parsed = json.loads(data_str)
                except Exception:
                    parsed = {"raw": data_str}
                events.append((current_event, parsed))
            current_event = None
            current_data = []

    if current_event and current_data:
        data_str = " ".join(current_data)
        try:
            parsed = json.loads(data_str)
        except Exception:
            parsed = {"raw": data_str}
        events.append((current_event, parsed))

    return events


async def create_user(client: AsyncClient, name: str = "StreamUser") -> tuple[str, str, dict[str, str]]:
    email = f"{name.lower()}_{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/auth/signup",
        json={"name": name, "email": email, "password": "Password123!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return user_id, token, {"Authorization": f"Bearer {token}"}


async def insert_doc(user_id: str) -> Document:
    doc_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        doc = Document(
            id=doc_id,
            user_id=user_id,
            filename="stream_doc.pdf",
            storage_key=f"documents/{user_id}/{doc_id}.pdf",
            file_url=f"http://test/storage/{doc_id}.pdf",
            file_size=2048,
            mime_type="application/pdf",
            status="indexed",
        )
        session.add(doc)
        await session.commit()
        await session.refresh(doc)
        return doc


class MockStreamingProvider(BaseAnswerProvider):
    def __init__(self, tokens: list[str], should_fail: bool = False):
        self.tokens = tokens
        self.should_fail = should_fail

    async def generate(self, query, context_chunks, conversation_context=None, *args, **kwargs) -> str:
        return "".join(self.tokens)

    async def generate_stream(self, query, context_chunks, conversation_context=None, *args, **kwargs) -> AsyncIterator[str]:
        for i, token in enumerate(self.tokens):
            if self.should_fail and i == 2:
                raise AnswerGenerationError("Simulated LLM stream disconnect")
            yield token


@pytest.mark.asyncio
async def test_stream_answer_events_order():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user(client, "Streamer1")
        doc = await insert_doc(user_id)

        mock_chunk = RerankedSearchResult(
            point_id="p1",
            document_id=doc.id,
            chunk_index=0,
            content="Search Sphere supports streaming SSE responses.",
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=9.1,
            score=9.1,
            rank=1,
        )
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search = AsyncMock(return_value=[mock_chunk])

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite = AsyncMock(
            return_value=RewriteResult(
                original_query="how does streaming work?",
                retrieval_query="how does streaming work?",
                rewritten=False,
            )
        )

        provider = MockStreamingProvider(tokens=["Streaming ", "is ", "fast! [1]"])
        generator = AnswerGenerator(provider=provider)

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: generator

        try:
            resp = await client.post(
                "/answer/stream",
                json={"query": "how does streaming work?"},
                headers=headers,
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            events = parse_sse_events(resp.text)
            event_types = [e[0] for e in events]

            # Verify structured event sequence: metadata -> source -> tokens -> done
            assert event_types[0] == "metadata"
            assert events[0][1]["query"] == "how does streaming work?"

            assert event_types[1] == "source"
            assert len(events[1][1]["sources"]) == 1

            token_events = [e for e in events if e[0] == "token"]
            tokens_text = "".join(e[1]["token"] for e in token_events)
            assert tokens_text == "Streaming is fast! [1]"

            assert event_types[-1] == "done"
            assert "Streaming is fast! [1]" in events[-1][1]["answer"]
        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)


@pytest.mark.asyncio
async def test_stream_in_conversation_persists_final_answer():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user(client, "Streamer2")
        doc = await insert_doc(user_id)

        conv_resp = await client.post("/conversations", json={}, headers=headers)
        conv_id = conv_resp.json()["id"]

        mock_chunk = RerankedSearchResult(
            point_id="p1",
            document_id=doc.id,
            chunk_index=0,
            content="Grounded context token.",
            token_count=5,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=8.0,
            score=8.0,
            rank=1,
        )
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search = AsyncMock(return_value=[mock_chunk])

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite = AsyncMock(
            return_value=RewriteResult(
                original_query="first question",
                retrieval_query="first question",
                rewritten=False,
            )
        )

        provider = MockStreamingProvider(tokens=["Answer ", "token ", "stream."])
        generator = AnswerGenerator(provider=provider)

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: generator

        try:
            resp = await client.post(
                f"/conversations/{conv_id}/answer/stream",
                json={"query": "first question"},
                headers=headers,
            )
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            event_types = [e[0] for e in events]

            assert "metadata" in event_types
            assert "source" in event_types
            assert "token" in event_types
            assert "done" in event_types

            # Verify that final answer was persisted in conversation history
            conv_detail = await client.get(f"/conversations/{conv_id}", headers=headers)
            assert conv_detail.status_code == 200
            messages = conv_detail.json()["messages"]
            assert len(messages) == 2
            assert messages[0]["role"] == "user"
            assert messages[0]["content"] == "first question"
            assert messages[1]["role"] == "assistant"
            assert messages[1]["content"] == "Answer token stream."
            assert len(messages[1]["sources"]) == 1
        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)


@pytest.mark.asyncio
async def test_stream_error_does_not_persist_partial_assistant_message():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        user_id, _, headers = await create_user(client, "Streamer3")
        doc = await insert_doc(user_id)

        conv_resp = await client.post("/conversations", json={}, headers=headers)
        conv_id = conv_resp.json()["id"]

        mock_chunk = RerankedSearchResult(
            point_id="p1",
            document_id=doc.id,
            chunk_index=0,
            content="Grounded context token.",
            token_count=5,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            rerank_score=8.0,
            score=8.0,
            rank=1,
        )
        mock_retriever = AsyncMock(spec=RerankedHybridRetriever)
        mock_retriever.search = AsyncMock(return_value=[mock_chunk])

        mock_rewriter = AsyncMock(spec=QueryRewriter)
        mock_rewriter.rewrite = AsyncMock(
            return_value=RewriteResult(
                original_query="crash query",
                retrieval_query="crash query",
                rewritten=False,
            )
        )

        # Provider will raise error mid-stream!
        provider = MockStreamingProvider(tokens=["Part 1", "Part 2", "CRASH"], should_fail=True)
        generator = AnswerGenerator(provider=provider)

        app.dependency_overrides[get_retriever] = lambda: mock_retriever
        app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
        app.dependency_overrides[get_answer_generator] = lambda: generator

        try:
            resp = await client.post(
                f"/conversations/{conv_id}/answer/stream",
                json={"query": "crash query"},
                headers=headers,
            )
            assert resp.status_code == 200
            events = parse_sse_events(resp.text)
            event_types = [e[0] for e in events]

            assert "error" in event_types
            assert "done" not in event_types

            # Ensure NO partial assistant message was saved! Only user question was saved.
            conv_detail = await client.get(f"/conversations/{conv_id}", headers=headers)
            assert conv_detail.status_code == 200
            messages = conv_detail.json()["messages"]
            assert len(messages) == 1
            assert messages[0]["role"] == "user"
            assert not any(m["role"] == "assistant" for m in messages)
        finally:
            app.dependency_overrides.pop(get_retriever, None)
            app.dependency_overrides.pop(get_query_rewriter, None)
            app.dependency_overrides.pop(get_answer_generator, None)
