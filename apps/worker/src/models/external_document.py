import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, JSON, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.db import Base


class ExternalDocument(Base):
    """
    Worker representation of externally owned documents (Quick Clinic medical documents).
    """

    __tablename__ = "external_documents"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    source_system: Mapped[str] = mapped_column(String(50), nullable=False, default="quick_clinic", index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    collection_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    owner_subject_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    external_document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    external_patient_id: Mapped[str] = mapped_column(String(128), nullable=False, default="default", index=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document_type: Mapped[str] = mapped_column(String(50), nullable=False)
    report_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(50), nullable=False, default="QUEUED", index=True)
    processing_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    content: Mapped["ExternalDocumentContent | None"] = relationship(
        "ExternalDocumentContent",
        back_populates="document",
        uselist=False,
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "source_system",
            "external_document_id",
            name="uq_external_documents_source_doc_id",
        ),
    )

    @property
    def client_id(self) -> str:
        return self.source_system

    @client_id.setter
    def client_id(self, val: str) -> None:
        self.source_system = val
