"""Authorized, bounded history selection using real PostgreSQL and synthetic data."""

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import delete, update
from src.inference.cohere_gateway import CohereGateway
from src.inference.policy import ProviderNotApprovedError
from src.models.conversation import Conversation
from src.models.document_chunk import DocumentChunk
from src.models.message import Message
from src.schemas.search import RewriteResult
from src.security.query_access import query_inference
from src.services.conversation_context import select_conversation_context
from src.services.query_rewriter import QueryRewriter
from test_query_access_postgres import indexed_document, native_access


async def conversation(fixture, row=None):
    cid = str(uuid.uuid4())
    timestamp = datetime.now(UTC) - timedelta(days=1)
    sources = [{"document_id": row.document_id, "chunk_index": row.chunk_index, "content": row.content}] if row else []
    texts = [("user", "Explain the maternity leave policy ML-2026."),
             ("assistant", "The maternity leave policy ML-2026 covers employees and interns [1].")]
    messages = [Message(id=str(uuid.uuid4()), conversation_id=cid, role=role, content=content,
                        sources=sources if role == "assistant" else None,
                        created_at=timestamp + timedelta(seconds=index)) for index, (role, content) in enumerate(texts)]
    async with fixture.sessions() as db, db.begin():
        db.add(Conversation(id=cid, user_id=fixture.owner_id))
        await db.flush()
        db.add_all(messages)
    access = native_access(fixture)
    access.conversation_id = cid
    return access, messages


@pytest.mark.asyncio
async def test_standalone_and_greeting_skip_all_history_queries():
    access = type("NoHistoryAllowed", (), {"sessions": lambda self: pytest.fail("History queried for standalone question")})()
    for query in ["Hello!", "Thanks", "What is maternity leave?", "Explain policy ML-2026"]:
        result = await select_conversation_context(access, query)
        assert result.requires_history is False and result.history == []


@pytest.mark.asyncio
async def test_interns_followup_uses_authorized_policy_turn_and_skips_greetings(fixture):
    _, row = await indexed_document(fixture)
    access, messages = await conversation(fixture, row)
    async with fixture.sessions() as db, db.begin():
        db.add_all([Message(conversation_id=access.conversation_id, role=role, content=text,
                            created_at=datetime.now(UTC) + timedelta(seconds=index))
                    for index, (role, text) in enumerate([("user", "Thanks!"), ("assistant", "You're welcome!")])])
    async with query_inference(access):
        selected = await select_conversation_context(access, "What about interns?")
        provider = type("SyntheticRewrite", (), {"rewrite": AsyncMock(return_value=RewriteResult(
            original_query="What about interns?", retrieval_query="What maternity leave does policy ML-2026 provide for interns?", rewritten=True,
        ))})()
        result = await QueryRewriter(provider=provider).rewrite("What about interns?", selected.history)
    assert selected.message_ids == [message.id for message in messages]
    assert result.retrieval_query == "What maternity leave does policy ML-2026 provide for interns?"
    assert row.point_id in access.evidence
    assert "welcome" not in str(provider.rewrite.await_args)


@pytest.mark.asyncio
async def test_exact_previous_answer_and_explicit_reference_never_substitute_revoked_evidence(fixture):
    _, row = await indexed_document(fixture)
    access, messages = await conversation(fixture, row)
    async with query_inference(access):
        selected = await select_conversation_context(access, "Summarize your previous answer")
        assert selected.exact_previous_answer == messages[1].content
        explicit = await select_conversation_context(access, "Explain response " + messages[1].id)
        assert explicit.exact_previous_answer == messages[1].content
    async with fixture.sessions() as db, db.begin():
        await db.execute(delete(DocumentChunk).where(DocumentChunk.point_id == row.point_id))
    for query in ["Summarize your previous answer", "Explain response " + messages[1].id]:
        denied = await select_conversation_context(access, query)
        assert denied.clarification and denied.history == []


@pytest.mark.asyncio
async def test_topic_matching_finds_old_question_and_answer_without_unrelated_recent_turns(fixture):
    access, messages = await conversation(fixture)
    async with fixture.sessions() as db, db.begin():
        # The topic appears only in the old question, not the answer.
        await db.execute(update(Message).where(Message.id == messages[1].id).values(content="Yes, interns are eligible."))
        db.add_all([Message(conversation_id=access.conversation_id, role="user" if index % 2 == 0 else "assistant",
                            content="Unrelated travel discussion.", created_at=datetime.now(UTC) + timedelta(seconds=index))
                    for index in range(70)])
    selected = await select_conversation_context(access, "What about maternity eligibility?")
    assert selected.message_ids == [message.id for message in messages]
    assert all("travel" not in message.content for message in selected.history)


@pytest.mark.asyncio
async def test_other_conversation_message_ids_and_cross_user_access_are_denied(fixture):
    access, _ = await conversation(fixture)
    other, messages = await conversation(fixture)
    selected = await select_conversation_context(access, "Repeat response " + messages[1].id)
    assert selected.clarification and not selected.history
    access.context = type(access.context)("search_sphere", "another-user", user_id="another-user", is_service_client=False)
    with pytest.raises(HTTPException) as caught:
        await select_conversation_context(access, "What about interns?")
    assert caught.value.status_code == 404
    # An explicit question reference cannot silently select a different answer.
    selected = await select_conversation_context(other, "Explain message " + messages[0].id)
    assert selected.clarification and not selected.history


@pytest.mark.asyncio
async def test_context_change_or_evidence_replacement_denies_next_provider_call(fixture):
    _, row = await indexed_document(fixture)
    access, messages = await conversation(fixture, row)
    async with query_inference(access):
        await select_conversation_context(access, "What about interns?")
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(Message).where(Message.id == messages[0].id).values(content="Edited topic"))
        with pytest.raises(ProviderNotApprovedError, match="context changed"):
            await CohereGateway().embed(["PUBLIC SYNTHETIC"])
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(DocumentChunk).where(DocumentChunk.point_id == row.point_id).values(content="Replacement policy"))
    fresh = native_access(fixture)
    fresh.conversation_id = access.conversation_id
    selected = await select_conversation_context(fresh, "What about interns?")
    assert selected.clarification and not selected.history
