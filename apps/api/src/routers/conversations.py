from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.user import User
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.conversation import (
    ConversationAnswerRequest,
    ConversationAnswerResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationSummaryResponse,
    ConversationUpdate,
)
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter
from src.services.answer_generator import AnswerGenerator, get_answer_generator
from src.services.answer_service import AnswerService
from src.services.conversation_service import ConversationService
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import SearchService, get_retriever

router = APIRouter(prefix="/conversations", tags=["Conversations"])


def get_conversation_service(
    db: AsyncSession = Depends(get_db),
    retriever: RerankedHybridRetriever = Depends(get_retriever),
    query_rewriter: QueryRewriter = Depends(get_query_rewriter),
    answer_generator: AnswerGenerator = Depends(get_answer_generator),
) -> ConversationService:
    """Dependency provider for ConversationService."""
    search_service = SearchService(
        db=db,
        retriever=retriever,
        query_rewriter=query_rewriter,
    )
    answer_service = AnswerService(
        search_service=search_service,
        answer_generator=answer_generator,
    )
    return ConversationService(
        db=db,
        answer_service=answer_service,
    )


@router.post(
    "",
    response_model=ConversationSummaryResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation",
)
async def create_conversation(
    payload: ConversationCreate | None = None,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationSummaryResponse:
    """Create a persistent conversation thread for the authenticated user."""
    title = payload.title if payload else None
    return await service.create_conversation(user_id=current_user.id, title=title)


@router.get(
    "",
    response_model=list[ConversationSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="List user conversations",
)
async def list_conversations(
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> list[ConversationSummaryResponse]:
    """List all conversation threads belonging to the authenticated user."""
    return await service.list_conversations(user_id=current_user.id)


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    status_code=status.HTTP_200_OK,
    summary="Get conversation with message history",
)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationResponse:
    """Fetch conversation details and historical messages. Enforces tenant isolation."""
    return await service.get_conversation(
        conversation_id=conversation_id, user_id=current_user.id
    )


@router.put(
    "/{conversation_id}",
    response_model=ConversationSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="Update conversation title",
)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationSummaryResponse:
    """Update title of an existing conversation thread."""
    return await service.update_title(
        conversation_id=conversation_id,
        title=payload.title,
        user_id=current_user.id,
    )


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete conversation",
)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> None:
    """Delete a conversation and cascade delete all its messages."""
    await service.delete_conversation(
        conversation_id=conversation_id, user_id=current_user.id
    )


@router.post(
    "/{conversation_id}/answer",
    response_model=ConversationAnswerResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limiter.check)],
    summary="Ask a question within conversation",
)
async def answer_in_conversation(
    conversation_id: str,
    request: ConversationAnswerRequest,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> ConversationAnswerResponse:
    """
    Ask a question within an ongoing conversation:
    - Injects recent conversation history into query rewriting and RAG synthesis;
    - Automatically persists user question and assistant answer with citations;
    - Enforces tenant isolation and cascades failures appropriately.
    """
    return await service.answer_in_conversation(
        conversation_id=conversation_id,
        request=request,
        user=current_user,
    )


@router.post(
    "/{conversation_id}/answer/stream",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limiter.check)],
    summary="Stream answer within conversation via Server-Sent Events (SSE)",
)
async def stream_answer_in_conversation(
    conversation_id: str,
    request: ConversationAnswerRequest,
    current_user: User = Depends(get_current_user),
    service: ConversationService = Depends(get_conversation_service),
) -> StreamingResponse:
    """
    Stream grounded RAG answer within a conversation thread as Server-Sent Events (SSE):
    - Yields structured SSE events: metadata, source, token, done, error;
    - Strictly commits the final assistant message ONLY upon full completion;
    - Enforces tenant isolation.
    """
    return StreamingResponse(
        service.stream_answer_in_conversation(
            conversation_id=conversation_id,
            request=request,
            user=current_user,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

