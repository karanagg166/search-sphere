import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    column,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base

if TYPE_CHECKING:
    from src.models.message import Message
    from src.models.user import User


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint("(user_id IS NOT NULL AND client_id IS NULL AND tenant_id IS NULL AND actor_id IS NULL AND external_reference IS NULL) OR (user_id IS NULL AND client_id IS NOT NULL AND tenant_id IS NOT NULL AND actor_id IS NOT NULL AND external_reference IS NOT NULL)", name="ck_conversation_identity"),
        Index("uq_delegated_conversation_reference", "client_id", "tenant_id", "actor_id", func.coalesce(column("collection_id"), ""), func.coalesce(column("subject_id"), ""), "external_reference", unique=True),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    client_id: Mapped[str | None] = mapped_column(String(64))
    tenant_id: Mapped[str | None] = mapped_column(String(128))
    actor_id: Mapped[str | None] = mapped_column(String(255))
    collection_id: Mapped[str | None] = mapped_column(String(128))
    subject_id: Mapped[str | None] = mapped_column(String(255))
    external_reference: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(
        String(255),
        default="New Conversation",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped["User | None"] = relationship("User", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation id={self.id} user_id={self.user_id} title={self.title}>"
