"""Idempotent registration and scoped access using existing conversation/message tables."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import delete, select, text

from src.models.conversation import Conversation
from src.models.message import Message
from src.security.conversation_access import conversation_query
from src.security.data_screening import contains_secrets
from src.services.conversation_context import message_evidence


async def get_conversation(db, context, conversation_id, *, lock=False):
    query = conversation_query(context).where(Conversation.id == conversation_id)
    row = (await db.execute(query.with_for_update() if lock else query)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return row


async def register_conversation(db, context, external_reference=None):
    query = conversation_query(context)
    if not context.is_service_client:
        raise HTTPException(status_code=403, detail="Delegated conversation registration requires a service client.")
    reference = external_reference or str(uuid.uuid4())
    # The scoped registration lock contains IDs only, never content/credentials.
    lock = "/".join([context.client_id, context.tenant_id, context.actor_id, context.collection_id or "", context.subject_id or "", reference])
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 32))"), {"key": lock})
    row = (await db.execute(query.where(Conversation.external_reference == reference))).scalar_one_or_none()
    if row is None:
        row = Conversation(client_id=context.client_id, tenant_id=context.tenant_id, actor_id=context.actor_id,
            collection_id=context.collection_id, subject_id=context.subject_id, external_reference=reference)
        db.add(row)
        await db.flush()
    return row


async def visible_messages(db, access, conversation_id, limit=50, offset=0):
    await get_conversation(db, access.context, conversation_id)
    rows = (await db.execute(select(Message).where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id).offset(offset).limit(min(limit, 100)))).scalars().all()
    result = []
    for message in rows:
        references = await message_evidence(db, access, message) if message.role == "assistant" else {}
        valid = references is not None and not contains_secrets(message.content)
        if valid:
            access.evidence.update(references)
        result.append({"id": message.id, "role": message.role,
            "content": message.content if valid else "This answer is unavailable because its source access or version changed.",
            "sources": message.sources if valid else [], "created_at": message.created_at, "sources_available": valid})
    await access.validate()
    return result


async def append_turn(db, access, query, answer, sources, retrieval_query=None, rewritten=False):
    """Persist one completed turn atomically, after current authorization checks."""
    if not access.conversation_id:
        return None
    await access.validate()
    conversation = await get_conversation(db, access.context, access.conversation_id, lock=True)
    now = datetime.now(UTC)
    question = Message(id=str(uuid.uuid4()), conversation_id=conversation.id, role="user", content=query,
                       original_query=query, created_at=now)
    response = Message(id=str(uuid.uuid4()), conversation_id=conversation.id, role="assistant", content=answer,
                       original_query=query, retrieval_query=retrieval_query, rewritten=rewritten,
                       sources=sources, created_at=now + timedelta(microseconds=1))
    db.add_all([question, response])
    conversation.updated_at = now
    await db.flush()
    return response


async def remove_conversation(db, context, conversation_id):
    row = await get_conversation(db, context, conversation_id, lock=True)
    await db.execute(delete(Message).where(Message.conversation_id == row.id))
    await db.delete(row)
