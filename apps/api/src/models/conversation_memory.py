from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ConversationMemory(Base):
    __tablename__ = "conversation_memory"

    conversation_id: Mapped[str] = mapped_column(String(36), ForeignKey("conversations.id",ondelete="CASCADE"),primary_key=True)
    state: Mapped[dict] = mapped_column(JSON().with_variant(JSONB,"postgresql"),nullable=False,default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),nullable=False,server_default=func.now(),onupdate=func.now())
