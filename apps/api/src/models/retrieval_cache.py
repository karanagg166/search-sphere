from datetime import datetime

from sqlalchemy import JSON, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class RetrievalCache(Base):
    __tablename__ = "retrieval_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    entries: Mapped[list] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
