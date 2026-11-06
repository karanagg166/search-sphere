"""Conversation ownership binds the actor separately from resource delegation."""

from fastapi import HTTPException
from sqlalchemy import select

from src.models.conversation import Conversation


def conversation_query(context):
    query = select(Conversation)
    if not context.is_service_client:
        return query.where(Conversation.user_id == context.user_id, Conversation.client_id.is_(None))
    if not context.actor_id or not context.actor_id.strip():
        raise HTTPException(status_code=422, detail="X-Actor-ID is required for delegated conversations.")
    return query.where(
        Conversation.user_id.is_(None), Conversation.client_id == context.client_id,
        Conversation.tenant_id == context.tenant_id, Conversation.actor_id == context.actor_id,
        Conversation.collection_id.is_not_distinct_from(context.collection_id),
        Conversation.subject_id.is_not_distinct_from(context.subject_id),
    )
