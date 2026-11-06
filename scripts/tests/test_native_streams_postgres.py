"""No native response content precedes generation and current permission checks."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select, update
from src.inference.policy import ProviderQuotaExceededError
from src.models.conversation import Conversation
from src.models.document import Document
from src.models.message import Message
from src.models.user import User
from src.processing.models.search import RerankedSearchResult
from src.schemas.answer import AnswerRequest
from src.schemas.conversation import ConversationAnswerRequest
from src.schemas.search import RewriteResult
from src.security.query_access import current_query_access, query_inference
from src.services.answer_service import AnswerService
from src.services.conversation_service import ConversationService
from src.services.search_service import SearchService
from test_query_access_postgres import indexed_document, native_access


def frames(events):
    return [(event.split("\n",1)[0][7:],json.loads(event.split("\ndata: ",1)[1])) for event in events]


async def setup(fixture, *, fail=False, empty=False):
    doc,row=await indexed_document(fixture)
    cid=str(uuid.uuid4())
    async with fixture.sessions() as db,db.begin():
        db.add(Conversation(id=cid,user_id=fixture.owner_id))
        user=await db.get(User,fixture.owner_id)
    async def retrieve(**kwargs):
        candidate=RerankedSearchResult(row.point_id,row.document_id,row.chunk_index,row.content,row.token_count,
            row.start_page,row.end_page,row.page_numbers,row.block_types,0.99,rrf_score=0.01)
        return await current_query_access().authorize_candidates([candidate])
    retriever=SimpleNamespace(search=AsyncMock(side_effect=retrieve))
    rewriter=SimpleNamespace(rewrite=AsyncMock(side_effect=lambda query,conversation_context:RewriteResult(original_query=query,retrieval_query=query)))
    async def partial(**kwargs):
        async def tokens():
            if empty:
                return
            yield "PRIVATE UNVALIDATED PARTIAL" if fail else "Interns are covered [1]."
            if fail:
                raise ProviderQuotaExceededError("PRIVATE PROVIDER DIAGNOSTIC")
        return tokens(),kwargs["chunks"]
    generator=SimpleNamespace(generate_answer_stream=AsyncMock(side_effect=partial),generate_answer=AsyncMock(side_effect=AssertionError("No generation fallback")))
    return SimpleNamespace(doc=doc,row=row,cid=cid,user=user,retriever=retriever,rewriter=rewriter,generator=generator)


@pytest.mark.asyncio
@pytest.mark.parametrize('conversation',[False,True])
async def test_provider_error_after_partial_tokens_emits_only_safe_error_and_no_turn(fixture,conversation):
    value=await setup(fixture,fail=True)
    access=native_access(fixture)
    access.conversation_id=value.cid if conversation else None
    async with fixture.sessions() as db,query_inference(access):
        service=AnswerService(SearchService(db,value.retriever,value.rewriter),value.generator)
        stream=ConversationService(db,service).stream_answer_in_conversation(value.cid,ConversationAnswerRequest(query="Explain maternity policy"),value.user) if conversation else service.stream_answer(AnswerRequest(query="Explain maternity policy"),value.user)
        events=[event async for event in stream]
    parsed=frames(events)
    assert len(parsed)==1 and parsed[0][0]=="error" and parsed[0][1]["code"]=="provider_quota"
    assert "PRIVATE" not in str(events)
    value.generator.generate_answer_stream.assert_awaited_once()
    value.generator.generate_answer.assert_not_called()
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==value.cid))==0


@pytest.mark.asyncio
async def test_empty_stream_cannot_trigger_second_inference_or_success_completion(fixture):
    value=await setup(fixture,empty=True)
    async with fixture.sessions() as db,query_inference(native_access(fixture)):
        service=AnswerService(SearchService(db,value.retriever,value.rewriter),value.generator)
        events=[event async for event in service.stream_answer(AnswerRequest(query="Explain maternity policy"),value.user)]
    assert [kind for kind,_ in frames(events)]==["error"]
    value.generator.generate_answer_stream.assert_awaited_once()
    value.generator.generate_answer.assert_not_called()


@pytest.mark.asyncio
async def test_permission_revocation_between_metadata_and_sources_prevents_later_frames(fixture):
    value=await setup(fixture)
    async with fixture.sessions() as db,query_inference(native_access(fixture)):
        service=AnswerService(SearchService(db,value.retriever,value.rewriter),value.generator)
        stream=service.stream_answer(AnswerRequest(query="Explain maternity policy"),value.user)
        first=await anext(stream)
        assert frames([first])[0][0]=="metadata"
        async with fixture.sessions() as changed,changed.begin():
            await changed.execute(update(Document).where(Document.id==value.doc.id).values(status="failed"))
        rest=[event async for event in stream]
    assert [kind for kind,_ in frames(rest)]==["error"]
    assert "Interns are covered" not in str(rest) and value.row.content not in str(rest)


@pytest.mark.asyncio
async def test_completed_conversation_stream_saves_one_atomic_turn_with_identical_answer(fixture):
    value=await setup(fixture)
    access=native_access(fixture)
    access.conversation_id=value.cid
    async with fixture.sessions() as db,query_inference(access):
        service=ConversationService(db,AnswerService(SearchService(db,value.retriever,value.rewriter),value.generator))
        events=frames([event async for event in service.stream_answer_in_conversation(value.cid,ConversationAnswerRequest(query="Explain maternity policy"),value.user)])
    assert [kind for kind,_ in events]==["metadata","source","token","done"]
    assert events[2][1]["token"]==events[3][1]["answer"] and events[3][1]["message_id"]
    async with fixture.sessions() as db:
        messages=(await db.execute(select(Message).where(Message.conversation_id==value.cid).order_by(Message.created_at))).scalars().all()
    assert [message.role for message in messages]==["user","assistant"]
    assert messages[1].content==events[3][1]["answer"]
