"""Compact memory is atomic, scoped, bounded and reauthorizes its message pointers."""

import asyncio
import json
import uuid
from dataclasses import replace

import pytest
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import delete, func, select
from src.models.conversation import Conversation
from src.models.conversation_memory import ConversationMemory
from src.models.document_chunk import DocumentChunk
from src.models.message import Message
from src.schemas.query_route import QueryRoute
from src.services.compact_memory import relevant_memory_messages
from src.services.conversation_context import select_conversation_context
from src.services.delegated_conversations import append_turn, register_conversation
from src.services.query_intent import QueryIntent
from test_delegated_conversations_postgres import identity
from test_query_access_postgres import indexed_document, native_access


async def native(fixture):
    doc,row=await indexed_document(fixture)
    cid=str(uuid.uuid4())
    async with fixture.sessions() as db,db.begin():
        db.add(Conversation(id=cid,user_id=fixture.owner_id))
    access=native_access(fixture)
    access.conversation_id=cid
    return access,doc,row


async def append(fixture,access,query,answer,sources):
    async with fixture.sessions() as db,db.begin():
        return await append_turn(db,access,query,answer,sources)


@pytest.mark.asyncio
async def test_memory_stores_topics_intents_points_and_provenance_without_source_passages(fixture):
    access,doc,row=await native(fixture)
    response=await append(fixture,access,"Explain maternity leave policy", "Interns are covered [1].",
        [{"document_id":doc.id,"chunk_index":row.chunk_index,"content":row.content}])
    async with fixture.sessions() as db:
        memory=await db.get(ConversationMemory,access.conversation_id)
    state=memory.state
    assert state["recent_intents"]==["RAG"] and state["latest_message_id"]==response.id
    turn=state["topic_turns"][0]
    assert "maternity" in turn["topics"] and "interns" in turn["answer_points"]
    reference=turn["sources"][0]
    assert reference["source_message_id"]==response.id and reference["document_id"]==doc.id
    assert reference["chunk_id"]==row.point_id and len(reference["source_sha256"])==64
    assert "content" not in reference and row.content not in json.dumps(state)


@pytest.mark.asyncio
async def test_rolled_back_turn_never_leaves_memory_or_messages(fixture):
    access,_doc,_row=await native(fixture)
    async with fixture.sessions() as db:
        await append_turn(db,access,"Synthetic question","Synthetic response",[])
        await db.rollback()
    async with fixture.sessions() as db:
        assert await db.get(ConversationMemory,access.conversation_id) is None
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==access.conversation_id))==0


@pytest.mark.asyncio
async def test_memory_keeps_a_substantive_topic_across_greetings_and_rechecks_its_source(fixture):
    access,doc,row=await native(fixture)
    response=await append(fixture,access,"Explain maternity leave", "Interns are covered [1].",
        [{"document_id":doc.id,"chunk_index":row.chunk_index,"content":row.content}])
    access.intent=QueryIntent(QueryRoute.DIRECT,response="You're welcome.")
    for _ in range(34):
        await append(fixture,access,"Thanks","You're welcome.",[])
    selected=await select_conversation_context(access,"What about interns?")
    assert selected.message_ids[-1]==response.id and len(selected.history)==2
    assert "maternity" in selected.history[0].content
    async with fixture.sessions() as db,db.begin():
        await db.execute(delete(DocumentChunk).where(DocumentChunk.point_id==row.point_id))
    denied=await select_conversation_context(access,"What about interns?")
    assert denied.clarification and denied.history==[]


@pytest.mark.asyncio
async def test_memory_ids_are_scoped_by_conversation_owner_and_delegated_actor(fixture):
    access,_doc,_row=await native(fixture)
    await append(fixture,access,"Explain maternity leave","Public synthetic response",[])
    wrong=replace(access,context=replace(access.context,user_id="other-user"))
    async with fixture.sessions() as db:
        assert await relevant_memory_messages(db,wrong,"What about maternity?")==[]
    context,_key,digest=await identity(fixture)
    async with fixture.sessions() as db,db.begin():
        cid=(await register_conversation(db,context,"synthetic-thread")).id
    from src.security.query_access import QueryAccess
    delegated=QueryAccess(fixture.sessions,context,"answers:generate",key_hash=digest,conversation_id=cid)
    await append(fixture,delegated,"Explain maternity leave","Public synthetic response",[])
    async with fixture.sessions() as db:
        assert await relevant_memory_messages(db,delegated,"What about maternity?")
        denied=replace(delegated,context=replace(context,actor_id="another-doctor"))
        assert await relevant_memory_messages(db,denied,"What about maternity?")==[]


@pytest.mark.asyncio
async def test_memory_is_bounded_and_cascades_with_the_conversation(fixture):
    access,_doc,_row=await native(fixture)
    for index in range(12):
        await append(fixture,access,f"Explain public-topic-{index}",f"Public response point-{index}",[])
    async with fixture.sessions() as db:
        memory=await db.get(ConversationMemory,access.conversation_id)
        assert len(memory.state["topic_turns"])==8 and len(memory.state["recent_intents"])==8
        assert len(json.dumps(memory.state).encode())<=32768
    async with fixture.sessions() as db,db.begin():
        await db.execute(delete(Conversation).where(Conversation.id==access.conversation_id))
    async with fixture.sessions() as db:
        assert await db.get(ConversationMemory,access.conversation_id) is None


@pytest.mark.asyncio
async def test_concurrent_completed_turns_do_not_lose_memory_updates(fixture):
    access,_doc,_row=await native(fixture)
    await asyncio.gather(*(append(fixture,access,f"Explain concurrent-topic-{index}",f"Public point-{index}",[]) for index in range(7)))
    async with fixture.sessions() as db:
        memory=await db.get(ConversationMemory,access.conversation_id)
        assert len(memory.state["topic_turns"])==7
        assert len({turn["assistant_message_id"] for turn in memory.state["topic_turns"]})==7
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==access.conversation_id))==14
