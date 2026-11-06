import json
from collections.abc import AsyncIterator

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.inference.policy import ProviderError
from src.models.user import User
from src.repositories.conversation_repository import ConversationRepository
from src.schemas.answer import AnswerRequest, AnswerSource
from src.schemas.conversation import (
    ConversationAnswerRequest,
    ConversationAnswerResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    MessageResponse,
)
from src.security.data_screening import contains_secrets
from src.security.query_access import QueryAccess, current_query_access
from src.security.response_stream import authorized_frames
from src.security.service_context import ServiceContext
from src.services.answer_service import AnswerService
from src.services.conversation_context import message_evidence
from src.services.delegated_conversations import append_turn

logger = structlog.get_logger()


class ConversationService:
    """
    Business service layer managing persistent conversations and chat history.
    Orchestrates conversation lifecycle and context-aware RAG question answering.
    """

    def __init__(
        self,
        db: AsyncSession,
        answer_service: AnswerService,
        repository: ConversationRepository | None = None,
    ) -> None:
        self.db = db
        self.answer_service = answer_service
        self.repo = repository or ConversationRepository(db)

    async def create_conversation(
        self, user_id: str, title: str | None = None
    ) -> ConversationSummaryResponse:
        """Create a new conversation for user."""
        conv = await self.repo.create(user_id=user_id, title=title)
        logger.info(
            "Conversation created",
            conversation_id=conv.id,
            user_id=user_id,
            title_length=len(conv.title),
        )
        return ConversationSummaryResponse(
            id=conv.id,
            user_id=conv.user_id,
            title=conv.title,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=0,
        )

    async def list_conversations(
        self, user_id: str
    ) -> list[ConversationSummaryResponse]:
        """List all conversations owned by the user."""
        rows = await self.repo.list_for_user(user_id)
        return [
            ConversationSummaryResponse(
                id=conv.id,
                user_id=conv.user_id,
                title=conv.title,
                created_at=conv.created_at,
                updated_at=conv.updated_at,
                message_count=count,
            )
            for conv, count in rows
        ]

    async def get_conversation(
        self, conversation_id: str, user_id: str
    ) -> ConversationResponse:
        """Fetch a specific conversation along with all its messages."""
        conv = await self.repo.get_by_id(
            conversation_id, user_id, include_messages=True
        )
        if not conv:
            logger.warning(
                "Conversation not found or access denied",
                conversation_id=conversation_id,
                user_id=user_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )

        access=QueryAccess(async_sessionmaker(self.db.bind,expire_on_commit=False),
            ServiceContext("search_sphere",user_id,subject_id=user_id,user_id=user_id,is_service_client=False),
            "conversations:read",conversation_id=conversation_id)
        available={}
        for message in conv.messages or []:
            references=await message_evidence(self.db,access,message) if message.role=="assistant" else {}
            valid=references is not None and not contains_secrets(message.content)
            valid=valid and not contains_secrets(json.dumps(message.sources or [],ensure_ascii=False))
            available[message.id]=valid
            if valid:
                access.evidence.update(references)
        await access.validate()
        messages = [
            MessageResponse(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=(
                    m.content
                    if available[m.id] and (m.content and m.content.strip())
                    else "This message is unavailable because its source access or version changed."
                ),
                original_query=m.original_query if available[m.id] else None,
                retrieval_query=m.retrieval_query if available[m.id] else None,
                rewritten=m.rewritten if available[m.id] else False,
                sources=[
                    AnswerSource(**s)
                    for s in ((m.sources or []) if available[m.id] else [])
                    if isinstance(s, dict)
                ],
                created_at=m.created_at,
            )
            for m in (conv.messages or [])
        ]

        return ConversationResponse(
            id=conv.id,
            user_id=conv.user_id,
            title=conv.title,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            messages=messages,
        )

    async def update_title(
        self, conversation_id: str, title: str, user_id: str
    ) -> ConversationSummaryResponse:
        """Update conversation title."""
        conv = await self.repo.update_title(conversation_id, user_id, title)
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )
        return ConversationSummaryResponse(
            id=conv.id,
            user_id=conv.user_id,
            title=conv.title,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            message_count=0,
        )

    async def delete_conversation(self, conversation_id: str, user_id: str) -> None:
        """Delete conversation and cascade delete its messages."""
        deleted = await self.repo.delete(conversation_id, user_id)
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )
        logger.info(
            "Conversation deleted",
            conversation_id=conversation_id,
            user_id=user_id,
        )

    async def answer_in_conversation(
        self,
        conversation_id: str,
        request: ConversationAnswerRequest,
        user: User,
        *, buffer_stream: bool = False,
    ) -> ConversationAnswerResponse:
        """
        Ask a question within an existing conversation, incorporating recent chat history.
        """
        # 1. Enforce tenant isolation and verify existence
        conv = await self.repo.get_by_id(
            conversation_id, user.id, include_messages=False
        )
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found.",
            )

        # 5. Delegate RAG answer generation to existing AnswerService
        answer_req = AnswerRequest(
            query=request.query,
            conversation_id=conversation_id,
            conversation_context=None,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_id=request.document_id,
            score_threshold=request.score_threshold,
        )
        answer_resp = await self.answer_service.generate_answer(request=answer_req,user=user,buffer_stream=buffer_stream)
        access=current_query_access()
        if access is None:
            raise HTTPException(status_code=403,detail="Conversation authorization is required.")
        sources_payload=[source.model_dump() for source in answer_resp.sources]
        assistant_msg=await append_turn(self.db,access,request.query,answer_resp.answer,sources_payload,
            answer_resp.retrieval_query,answer_resp.rewritten)
        for source in answer_resp.sources:
            source.source_message_id = assistant_msg.id
        if conv.title in ("New Conversation", ""):
            conv.title=request.query[:50].strip()+("..." if len(request.query)>50 else "")
        await self.db.commit()
        await access.validate()

        msg_resp = MessageResponse(
            id=assistant_msg.id,
            conversation_id=assistant_msg.conversation_id,
            role=assistant_msg.role,
            content=assistant_msg.content,
            original_query=assistant_msg.original_query,
            retrieval_query=assistant_msg.retrieval_query,
            rewritten=assistant_msg.rewritten,
            sources=answer_resp.sources,
            created_at=assistant_msg.created_at,
        )

        return ConversationAnswerResponse(
            conversation_id=conversation_id,
            message=msg_resp,
            query=answer_resp.query,
            retrieval_query=answer_resp.retrieval_query,
            rewritten=answer_resp.rewritten,
            answer=answer_resp.answer,
            sources=answer_resp.sources,
            requires_history=answer_resp.requires_history,
            route=answer_resp.route,
            context_message_ids=answer_resp.context_message_ids,
        )

    async def stream_answer_in_conversation(
        self,
        conversation_id: str,
        request: ConversationAnswerRequest,
        user: User,
    ) -> AsyncIterator[str]:
        """
        Stream SSE formatted events for a question in a conversation.
        Only commits the final assistant message if generation finishes without error.
        """
        try:
            result=await self.answer_in_conversation(conversation_id,request,user,buffer_stream=True)
            access=current_query_access()
            if access is not None:
                await access.validate()
        except (ProviderError,HTTPException) as exc:
            code=exc.code if isinstance(exc,ProviderError) else "answer_unavailable"
            yield f"event: error\ndata: {json.dumps({'code':code,'error':'Unable to generate answer.'})}\n\n"
            return
        payload=result.model_dump(mode="json")
        metadata={key:payload[key] for key in ['query','retrieval_query','rewritten','requires_history','route','context_message_ids']}
        frames=[("metadata",metadata),("source",{"sources":payload["sources"]}),("token",{"token":result.answer}),
                ("done",{"message_id":result.message.id,"answer":result.answer,"sources":payload["sources"]})]
        async for frame in authorized_frames(frames):
            yield frame
