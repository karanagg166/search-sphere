from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.db import get_db
from src.schemas.v1.conversations import ConversationCreate, ConversationResponse
from src.security.query_access import QueryAccess, query_inference
from src.security.service_auth import get_service_context, hash_api_key
from src.security.service_context import ServiceContext
from src.services.delegated_conversations import (
    get_conversation,
    register_conversation,
    remove_conversation,
    visible_messages,
)

router = APIRouter(prefix="/api/v1/conversations", tags=["V1 Conversations"])


def response(row):
    return ConversationResponse(id=row.id, external_reference=row.external_reference, client_id=row.client_id,
        tenant_id=row.tenant_id, actor_id=row.actor_id, collection_id=row.collection_id, owner_subject_id=row.subject_id)


@router.post("", response_model=ConversationResponse, status_code=201)
async def register(body: ConversationCreate, db: AsyncSession = Depends(get_db),
    context: ServiceContext = Depends(get_service_context(required_scopes={"conversations:write"}))):
    context = context.resolve_scope(body.collection_id, body.owner_subject_id)
    row = await register_conversation(db, context, body.external_reference)
    result = response(row)
    await db.commit()
    return result


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get(conversation_id: str, db: AsyncSession = Depends(get_db),
    context: ServiceContext = Depends(get_service_context(required_scopes={"conversations:read"}))):
    return response(await get_conversation(db, context, conversation_id))


@router.get("/{conversation_id}/messages")
async def messages(conversation_id: str, request: Request, limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0), db: AsyncSession = Depends(get_db),
    context: ServiceContext = Depends(get_service_context(required_scopes={"conversations:read"}))):
    await get_conversation(db, context, conversation_id)
    access = QueryAccess(async_sessionmaker(db.bind, expire_on_commit=False), context, "conversations:read",
        key_hash=hash_api_key(request.headers.get("Authorization", "")[7:].strip()), conversation_id=conversation_id)
    async with query_inference(access):
        items = await visible_messages(db, access, conversation_id, limit, offset)
    return {"messages": items, "limit": limit, "offset": offset}


@router.delete("/{conversation_id}")
async def remove(conversation_id: str, db: AsyncSession = Depends(get_db),
    context: ServiceContext = Depends(get_service_context(required_scopes={"conversations:write"}))):
    await remove_conversation(db, context, conversation_id)
    await db.commit()
    return {"deleted": True}
