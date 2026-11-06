"""Content-free tombstone and durable private-object cleanup receipt."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class DocumentDeletion(Base):
    __tablename__ = "document_deletions"
    __table_args__ = (UniqueConstraint("kind", "client_id", "tenant_id", "document_id", name="uq_document_deletion_identity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    document_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    collection_id: Mapped[str | None] = mapped_column(String(128))
    subject_id: Mapped[str | None] = mapped_column(String(128))
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    object_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
