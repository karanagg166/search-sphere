import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.models.feedback import Feedback
from src.models.user import User
from src.repositories.conversation_repository import ConversationRepository
from src.repositories.feedback_repository import FeedbackRepository
from src.schemas.feedback import FeedbackCreate, FeedbackResponse
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter

logger = structlog.get_logger()

router = APIRouter(tags=["Feedback"])


@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter.check)],
    summary="Submit answer rating or feedback",
)
async def submit_feedback(
    payload: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    """Submit rating and optional comment for a RAG answer."""
    repo = FeedbackRepository(db)

    # If conversation_id is provided, verify ownership
    if payload.conversation_id:
        conv_repo = ConversationRepository(db)
        conv = await conv_repo.get_by_id(payload.conversation_id, current_user.id)
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation '{payload.conversation_id}' not found.",
            )

    feedback = Feedback(
        user_id=current_user.id,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    created = await repo.create(feedback)
    logger.info(
        "User feedback recorded",
        user_id=current_user.id,
        rating=created.rating,
        feedback_id=created.id,
    )
    return FeedbackResponse.model_validate(created)


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limiter.check)],
    summary="Submit feedback for conversation message",
)
async def submit_message_feedback(
    conversation_id: str,
    message_id: str,
    payload: FeedbackCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FeedbackResponse:
    """Submit rating specifically linked to a conversation and assistant message."""
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_by_id(conversation_id, current_user.id)
    if not conv:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation '{conversation_id}' not found.",
        )

    # Verify message belongs to this conversation
    message_exists = any(m.id == message_id for m in conv.messages)
    if not message_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message '{message_id}' not found in conversation '{conversation_id}'.",
        )

    repo = FeedbackRepository(db)
    feedback = Feedback(
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating=payload.rating,
        comment=payload.comment,
    )
    created = await repo.create(feedback)
    logger.info(
        "Message feedback recorded",
        user_id=current_user.id,
        conversation_id=conversation_id,
        message_id=message_id,
        rating=created.rating,
    )
    return FeedbackResponse.model_validate(created)


@router.get(
    "/feedback",
    response_model=list[FeedbackResponse],
    status_code=status.HTTP_200_OK,
    summary="List feedback submitted by current user",
)
async def list_feedback(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FeedbackResponse]:
    """Retrieve all feedback entries submitted by the authenticated user."""
    repo = FeedbackRepository(db)
    feedbacks, _ = await repo.list_for_user(
        user_id=current_user.id, limit=limit, offset=offset
    )
    return [FeedbackResponse.model_validate(f) for f in feedbacks]
