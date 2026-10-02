import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.feedback import Feedback

logger = structlog.get_logger()


class FeedbackRepository:
    """Repository managing persistence and retrieval of user feedback."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, feedback: Feedback) -> Feedback:
        """Persist a new feedback entry."""
        self.db.add(feedback)
        await self.db.commit()
        await self.db.refresh(feedback)
        return feedback

    async def get_by_id(self, feedback_id: str, user_id: str) -> Feedback | None:
        """Retrieve feedback by ID enforcing user isolation."""
        stmt = select(Feedback).where(
            Feedback.id == feedback_id,
            Feedback.user_id == user_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_for_message(self, message_id: str, user_id: str) -> Feedback | None:
        """Check if user has already submitted feedback for a specific message."""
        stmt = select(Feedback).where(
            Feedback.message_id == message_id,
            Feedback.user_id == user_id,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Feedback], int]:
        """List feedback submitted by a specific user with pagination."""
        count_stmt = select(func.count(Feedback.id)).where(Feedback.user_id == user_id)
        total_result = await self.db.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = (
            select(Feedback)
            .where(Feedback.user_id == user_id)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all()), total
