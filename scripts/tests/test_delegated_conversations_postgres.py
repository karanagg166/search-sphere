"""Delegated actor/resource isolation and registration against real PostgreSQL."""

import asyncio
import hashlib
from dataclasses import replace

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select, update
from src.db import get_db
from src.inference.policy import ProviderNotApprovedError
from src.main import app
from src.models.conversation import Conversation
from src.models.message import Message
from src.models.service_client import ServiceClient
from src.security.query_access import QueryAccess
from src.security.service_context import ServiceContext
from src.services.conversation_context import select_conversation_context
from src.services.delegated_conversations import (
    append_turn,
    get_conversation,
    register_conversation,
    remove_conversation,
    visible_messages,
)


async def identity(fixture):
    key = "synthetic-conversation-" + fixture.owner_id
    digest = hashlib.sha256(key.encode()).hexdigest()
    async with fixture.sessions() as db, db.begin():
        db.add(ServiceClient(client_id=fixture.owner_id, name="Synthetic delegated conversations", api_key_hash=digest,
            api_key_prefix="synthetic", allowed_scopes=["conversations:read", "conversations:write", "answers:generate"],
            authorized_tenants=[fixture.owner_id]))
    context = ServiceContext(fixture.owner_id, fixture.owner_id, actor_id="doctor-a", collection_id="records-a", subject_id="patient-a")
    return context, key, digest


@pytest.mark.asyncio
async def test_concurrent_registration_is_idempotent_but_actors_and_subjects_are_distinct(fixture):
    context, _, _ = await identity(fixture)
    async def register(ctx):
        async with fixture.sessions() as db, db.begin():
            return (await register_conversation(db, ctx, "consumer-thread-42")).id
    ids = await asyncio.gather(*(register(context) for _ in range(8)))
    assert len(set(ids)) == 1
    assert await register(replace(context, actor_id="doctor-b")) != ids[0]
    assert await register(replace(context, subject_id="patient-b")) != ids[0]
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation).where(Conversation.client_id == fixture.owner_id)) == 3


@pytest.mark.asyncio
async def test_every_identity_and_resource_dimension_is_required(fixture):
    context, _, _ = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "public-thread")).id
    for changes in [{"client_id": "other-app"}, {"tenant_id": "other-clinic"}, {"actor_id": "doctor-b"},
                    {"collection_id": "other-records"}, {"subject_id": "patient-b"}, {"subject_id": None}]:
        async with fixture.sessions() as db:
            with pytest.raises(HTTPException) as caught:
                await get_conversation(db, replace(context, **changes), cid)
            assert caught.value.status_code == 404
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as caught:
            await get_conversation(db, replace(context, actor_id=None), cid)
        assert caught.value.status_code == 422


@pytest.mark.asyncio
async def test_completed_turn_is_atomic_and_history_uses_delegated_scope(fixture):
    context, _, digest = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "public-thread")).id
    access = QueryAccess(fixture.sessions, context, "answers:generate", key_hash=digest, conversation_id=cid)
    async with fixture.sessions() as db, db.begin():
        await append_turn(db, access, "Explain maternity leave policy", "Interns are covered by this public synthetic policy.", [])
    selection = await select_conversation_context(access, "What about interns?")
    assert [message.role for message in selection.history] == ["user", "assistant"]
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 2
    # Failed transaction must not leave half a turn or a purported answer.
    async with fixture.sessions() as db:
        await append_turn(db, access, "Uncommitted question", "Uncommitted answer", [])
        await db.rollback()
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 2


@pytest.mark.asyncio
async def test_revoked_source_redacts_old_answer_and_revoked_client_cannot_reuse_history(fixture):
    context, _, digest = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "public-thread")).id
        db.add(Message(conversation_id=cid, role="assistant", content="Revoked evidence must never be returned.",
            sources=[{"document_id": "deleted-document", "chunk_index": 0, "content": "Old source"}]))
    access = QueryAccess(fixture.sessions, context, "conversations:read", key_hash=digest, conversation_id=cid)
    async with fixture.sessions() as db:
        messages = await visible_messages(db, access, cid)
        assert messages[0]["sources_available"] is False
        assert "Revoked evidence" not in messages[0]["content"] and messages[0]["sources"] == []
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(ServiceClient).where(ServiceClient.client_id == fixture.owner_id).values(allowed_scopes=[]))
    with pytest.raises(ProviderNotApprovedError):
        await access.validate()


@pytest.mark.asyncio
async def test_conversation_delete_removes_messages_and_denies_later_access(fixture):
    context, _, digest = await identity(fixture)
    async with fixture.sessions() as db, db.begin():
        cid = (await register_conversation(db, context, "public-thread")).id
    access = QueryAccess(fixture.sessions, context, "answers:generate", key_hash=digest, conversation_id=cid)
    async with fixture.sessions() as db, db.begin():
        await append_turn(db, access, "Public question", "Public answer", [])
    async with fixture.sessions() as db, db.begin():
        await remove_conversation(db, context, cid)
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 0
    with pytest.raises(ProviderNotApprovedError, match="Conversation access"):
        await access.validate()
    # A late consumer request must not resurrect an explicitly deleted thread.
    async with fixture.sessions() as db, db.begin():
        with pytest.raises(HTTPException) as caught:
            await register_conversation(db, context, "public-thread")
        assert caught.value.status_code == 409


@pytest.mark.asyncio
async def test_http_actor_required_scope_binding_and_pagination(fixture):
    context, key, _ = await identity(fixture)
    async def database():
        async with fixture.sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    headers = {"Authorization": "Bearer " + key, "X-Tenant-ID": context.tenant_id,
               "X-Collection-ID": context.collection_id, "X-Subject-ID": context.subject_id}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as client:
            response = await client.post("/api/v1/conversations", headers=headers, json={"external_reference": "consumer-thread"})
            assert response.status_code == 422
            headers["X-Actor-ID"] = context.actor_id
            response = await client.post("/api/v1/conversations", headers=headers, json={"external_reference": "consumer-thread"})
            assert response.status_code == 201
            cid = response.json()["id"]
            duplicate = await client.post("/api/v1/conversations", headers=headers, json={"external_reference": "consumer-thread"})
            assert duplicate.json()["id"] == cid
            other = await client.get("/api/v1/conversations/" + cid, headers={**headers, "X-Actor-ID": "doctor-b"})
            assert other.status_code == 404
            messages = await client.get(f"/api/v1/conversations/{cid}/messages?limit=1&offset=1", headers=headers)
            assert messages.status_code == 200 and messages.json()["offset"] == 1
            assert (await client.delete("/api/v1/conversations/" + cid, headers=headers)).status_code == 200
            late = await client.post("/api/v1/conversations", headers=headers, json={"external_reference": "consumer-thread"})
            assert late.status_code == 409
            replacement = await client.post("/api/v1/conversations", headers=headers, json={"external_reference": "new-consumer-thread"})
            assert replacement.status_code == 201
    finally:
        app.dependency_overrides.pop(get_db, None)
