"""Durable reference deletion, races and scope isolation on real PostgreSQL."""

import asyncio
from dataclasses import replace

import pytest
from fastapi import HTTPException
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select
from src.models.conversation import Conversation
from src.models.conversation_deletion import ConversationDeletion
from src.models.conversation_memory import ConversationMemory
from src.models.message import Message
from src.security.query_access import QueryAccess
from src.services.delegated_conversations import (
    append_turn,
    register_conversation,
    remove_conversation,
    remove_conversation_reference,
)
from test_delegated_conversations_postgres import identity


@pytest.mark.asyncio
async def test_reference_delete_clears_turns_memory_and_survives_new_sessions(fixture):
    context, _, digest = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "consumer-thread")).id
    access = QueryAccess(fixture.sessions, context, "answers:generate", key_hash=digest, conversation_id=cid)
    async with fixture.sessions() as db, db.begin():
        await append_turn(db, access, "Public question", "Synthetic public answer", [])
    async with fixture.sessions() as db:
        assert await db.get(ConversationMemory, cid) is not None
    for _ in range(2):
        async with fixture.sessions() as db, db.begin():
            await remove_conversation_reference(db, context, "consumer-thread")
    async with fixture.sessions() as db:
        assert await db.get(Conversation, cid) is None
        assert await db.get(ConversationMemory, cid) is None
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 0
        assert await db.scalar(select(func.count()).select_from(ConversationDeletion).where(
            ConversationDeletion.client_id == context.client_id)) == 1
    async with fixture.sessions() as db, db.begin():
        with pytest.raises(HTTPException) as caught:
            await register_conversation(db, context, "consumer-thread")
        assert caught.value.status_code == 409
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"client_id": "different-app"}, {"tenant_id": "different-tenant"},
    {"actor_id": "different-doctor"}, {"collection_id": "different-records"},
    {"subject_id": "different-patient"}, {"subject_id": None},
])
async def test_delete_reference_cannot_cross_any_scope_dimension(fixture, changes):
    context, _, _ = await identity(fixture)
    other = replace(context, **changes)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "shared-label")).id
    try:
        async with fixture.sessions() as db, db.begin():
            await remove_conversation_reference(db, other, "shared-label")
        async with fixture.sessions() as db, db.begin():
            assert (await register_conversation(db, context, "shared-label")).id == cid
    finally:
        # The intentionally different client is outside the shared fixture's owner cleanup.
        if "client_id" in changes:
            from sqlalchemy import delete
            async with fixture.sessions() as db, db.begin():
                await db.execute(delete(ConversationDeletion).where(ConversationDeletion.client_id == other.client_id))


@pytest.mark.asyncio
async def test_deleting_absent_reference_blocks_late_registration_but_not_a_new_thread(fixture):
    context, _, _ = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        await remove_conversation_reference(db, context, "never-submitted")
    async with fixture.sessions() as db, db.begin():
        with pytest.raises(HTTPException) as caught:
            await register_conversation(db, context, "never-submitted")
        assert caught.value.status_code == 409
        assert (await register_conversation(db, context, "new-thread")).id


@pytest.mark.asyncio
async def test_rolled_back_deletion_does_not_fence_or_remove_the_thread(fixture):
    context, _, _ = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "rollback-thread")).id
    async with fixture.sessions() as db:
        await remove_conversation_reference(db, context, "rollback-thread")
        await db.rollback()
    async with fixture.sessions() as db, db.begin():
        assert (await register_conversation(db, context, "rollback-thread")).id == cid
        assert await db.scalar(select(func.count()).select_from(ConversationDeletion).where(
            ConversationDeletion.client_id == context.client_id)) == 0


@pytest.mark.asyncio
async def test_racing_registration_and_both_delete_paths_never_resurrect(fixture):
    context, _, _ = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "racing-thread")).id

    async def operation(kind):
        async with fixture.sessions() as db, db.begin():
            try:
                if kind == "register":
                    await register_conversation(db, context, "racing-thread")
                elif kind == "id":
                    await remove_conversation(db, context, cid)
                else:
                    await remove_conversation_reference(db, context, "racing-thread")
            except HTTPException as exc:
                assert exc.status_code == (409 if kind == "register" else 404)

    await asyncio.wait_for(asyncio.gather(*(operation(kind) for kind in
        ["register", "id", "reference", "register", "reference", "id"])), timeout=10)
    async with fixture.sessions() as db:
        assert await db.get(Conversation, cid) is None
        assert await db.scalar(select(func.count()).select_from(ConversationDeletion).where(
            ConversationDeletion.client_id == context.client_id)) == 1
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as caught:
            await register_conversation(db, context, "racing-thread")
        assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_reference_hash_has_unambiguous_scope_boundaries(fixture):
    context, _, _ = await identity(fixture)
    # These collide with the old slash-joined lock key but represent distinct scopes.
    first = replace(context, actor_id="doctor/a", collection_id="records")
    second = replace(context, actor_id="doctor", collection_id="a/records")
    async with fixture.sessions() as db, db.begin():
        await remove_conversation_reference(db, first, "same-reference")
        assert (await register_conversation(db, second, "same-reference")).id


@pytest.mark.asyncio
async def test_missing_actor_cannot_create_a_deletion_fence(fixture):
    context, _, _ = await identity(fixture)
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as caught:
            await remove_conversation_reference(db, replace(context, actor_id=None), "thread")
        assert caught.value.status_code == 422
        assert await db.scalar(select(func.count()).select_from(ConversationDeletion).where(
            ConversationDeletion.client_id == context.client_id)) == 0
