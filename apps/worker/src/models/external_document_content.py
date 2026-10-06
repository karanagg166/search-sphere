from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class ExternalDocumentContent(Base):
    __tablename__ = "external_document_contents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    external_document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("external_documents.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    document: Mapped["ExternalDocument"] = relationship(
        "ExternalDocument",
        back_populates="content",
    )
