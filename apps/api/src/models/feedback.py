import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base

if TYPE_CHECKING:
    from src.models.conversation import Conversation
    from src.models.message import Message
    from src.models.user import User


class Feedback(Base):
    """
    User ratings and qualitative feedback for grounded RAG answers.
    Persisted to enable evaluation, alignment, and retrieval improvement.
    """

    __tablename__ = "feedbacks"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    message_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("messages.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    rating: Mapped[int] = mapped_column(
        Integer,  # 1 for positive (thumbs up), -1 for negative (thumbs down)
        nullable=False,
    )
    comment: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User")
    conversation: Mapped["Conversation | None"] = relationship("Conversation")
    message: Mapped["Message | None"] = relationship("Message")

    def __repr__(self) -> str:
        return f"<Feedback id={self.id} rating={self.rating} message_id={self.message_id}>"
