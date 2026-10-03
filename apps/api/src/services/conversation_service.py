import json
from collections.abc import AsyncIterator

import structlog
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
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
from src.schemas.search import ConversationMessage, SearchRequest
from src.services.answer_generator import NO_RESULTS_ANSWER, sanitize_citations
from src.services.answer_service import AnswerService

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
            title=conv.title,
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

        messages = [
            MessageResponse(
                id=m.id,
                conversation_id=m.conversation_id,
                role=m.role,
                content=(
                    m.content
                    if (m.content and m.content.strip())
                    else NO_RESULTS_ANSWER
                ),
                original_query=m.original_query,
                retrieval_query=m.retrieval_query,
                rewritten=m.rewritten,
                sources=[
                    AnswerSource(**s)
                    for s in (m.sources or [])
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

        # 2. Fetch recent conversation history for contextual query rewriting
        recent_messages = await self.repo.get_recent_messages(
            conversation_id=conversation_id,
            limit=settings.CONVERSATION_MAX_HISTORY_MESSAGES,
        )
        conversation_context = [
            ConversationMessage(role=m.role, content=m.content)
            for m in recent_messages
        ]

        # 3. Persist the user's question as a user message
        await self.repo.create_message(
            conversation_id=conversation_id,
            role="user",
            content=request.query,
            original_query=request.query,
        )

        # 4. If conversation still has default title, set title from first question
        if conv.title in ("New Conversation", ""):
            auto_title = request.query[:50].strip()
            if len(request.query) > 50:
                auto_title += "..."
            await self.repo.update_title(conversation_id, user.id, auto_title)

        # 5. Delegate RAG answer generation to existing AnswerService
        answer_req = AnswerRequest(
            query=request.query,
            conversation_context=conversation_context,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_id=request.document_id,
        )
        answer_resp = await self.answer_service.generate_answer(
            request=answer_req,
            user=user,
        )

        # 6. Persist assistant answer and source citations
        sources_payload = [s.model_dump() for s in answer_resp.sources]
        assistant_msg = await self.repo.create_message(
            conversation_id=conversation_id,
            role="assistant",
            content=answer_resp.answer,
            original_query=answer_resp.query,
            retrieval_query=answer_resp.retrieval_query,
            rewritten=answer_resp.rewritten,
            sources=sources_payload,
        )

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
        # 1. Enforce tenant isolation and verify existence
        conv = await self.repo.get_by_id(
            conversation_id, user.id, include_messages=False
        )
        if not conv:
            yield f"event: error\ndata: {json.dumps({'error': 'Conversation not found.'})}\n\n"
            return

        # 2. Fetch recent conversation history
        recent_messages = await self.repo.get_recent_messages(
            conversation_id=conversation_id,
            limit=settings.CONVERSATION_MAX_HISTORY_MESSAGES,
        )
        conversation_context = [
            ConversationMessage(role=m.role, content=m.content)
            for m in recent_messages
        ]

        # 3. Persist the user's question
        await self.repo.create_message(
            conversation_id=conversation_id,
            role="user",
            content=request.query,
            original_query=request.query,
        )

        # 4. If conversation still has default title, set title from first question
        if conv.title in ("New Conversation", ""):
            auto_title = request.query[:50].strip()
            if len(request.query) > 50:
                auto_title += "..."
            await self.repo.update_title(conversation_id, user.id, auto_title)

        # 5. Search & retrieve chunks
        search_req = SearchRequest(
            query=request.query,
            conversation_context=conversation_context,
            top_k=request.top_k,
            candidate_k=request.candidate_k,
            document_id=request.document_id,
        )
        search_response = await self.answer_service.search_service.search(
            request=search_req,
            user=user,
        )

        metadata_payload = {
            "query": search_response.query,
            "retrieval_query": search_response.retrieval_query,
            "rewritten": search_response.rewritten,
        }
        yield f"event: metadata\ndata: {json.dumps(metadata_payload)}\n\n"

        if not search_response.results:
            yield f"event: source\ndata: {json.dumps({'sources': []})}\n\n"
            yield f"event: token\ndata: {json.dumps({'token': NO_RESULTS_ANSWER})}\n\n"
            msg = await self.repo.create_message(
                conversation_id=conversation_id,
                role="assistant",
                content=NO_RESULTS_ANSWER,
                original_query=search_response.query,
                retrieval_query=search_response.retrieval_query,
                rewritten=search_response.rewritten,
                sources=[],
            )
            yield f"event: done\ndata: {json.dumps({'message_id': msg.id, 'answer': NO_RESULTS_ANSWER, 'sources': []})}\n\n"
            return

        usable_chunks = search_response.results[: self.answer_service.answer_generator.max_context_chunks]
        sources = [
            AnswerSource(
                source_id=idx + 1,
                document_id=chunk.document_id,
                chunk_index=chunk.chunk_index,
                start_page=chunk.start_page,
                end_page=chunk.end_page,
                content=chunk.content,
                rerank_score=chunk.rerank_score,
            )
            for idx, chunk in enumerate(usable_chunks)
        ]
        sources_payload = [s.model_dump() for s in sources]
        yield f"event: source\ndata: {json.dumps({'sources': sources_payload})}\n\n"

        accumulated_tokens: list[str] = []
        stream_completed = False
        try:
            token_iter, _ = await self.answer_service.answer_generator.generate_answer_stream(
                query=search_response.query,
                chunks=search_response.results,
                conversation_context=conversation_context,
            )
            async for token in token_iter:
                accumulated_tokens.append(token)
                yield f"event: token\ndata: {json.dumps({'token': token})}\n\n"
            stream_completed = True
        except Exception as exc:
            logger.error("Streaming conversation answer failed", error=str(exc), user_id=user.id)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            return

        if stream_completed:
            raw_answer = "".join(accumulated_tokens).strip()
            # If tokens were empty, attempt non-streaming fallback
            if not raw_answer:
                logger.warning(
                    "Stream completed with empty tokens; invoking non-streaming fallback",
                    user_id=user.id,
                )
                try:
                    fallback_answer, _ = await self.answer_service.answer_generator.generate_answer(
                        query=search_response.query,
                        chunks=usable_chunks,
                        conversation_context=conversation_context,
                    )
                    raw_answer = fallback_answer.strip() if fallback_answer else ""
                    if raw_answer:
                        yield f"event: token\ndata: {json.dumps({'token': raw_answer})}\n\n"
                except Exception as fb_exc:
                    logger.error("Fallback generation failed", error=str(fb_exc))

            if not raw_answer:
                raw_answer = NO_RESULTS_ANSWER
                yield f"event: token\ndata: {json.dumps({'token': raw_answer})}\n\n"

            clean_answer = sanitize_citations(raw_answer, max_source_id=len(sources))
            assistant_msg = await self.repo.create_message(
                conversation_id=conversation_id,
                role="assistant",
                content=clean_answer,
                original_query=search_response.query,
                retrieval_query=search_response.retrieval_query,
                rewritten=search_response.rewritten,
                sources=sources_payload,
            )
            yield f"event: done\ndata: {json.dumps({'message_id': assistant_msg.id, 'answer': clean_answer, 'sources': sources_payload})}\n\n"

