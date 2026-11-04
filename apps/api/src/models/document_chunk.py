import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, UserDefinedType

from src.db import Base


class VectorType(UserDefinedType):
    """pgvector vector column type with dialect-aware serialization."""

    def __init__(self, dim: int = 384):
        self.dim = dim

    def get_col_spec(self, **kw):
        return f"vector({self.dim})"

    def bind_processor(self, dialect):
        def process(value):
            if value is None:
                return None
            if isinstance(value, (list, tuple)):
                if dialect.name == "postgresql":
                    return "[" + ",".join(str(float(x)) for x in value) + "]"
                return json.dumps(list(value))
            return str(value)

        return process

    def result_processor(self, dialect, coltype):
        def process(value):
            if value is None:
                return None
            if isinstance(value, str):
                cleaned = value.strip("[]")
                if not cleaned:
                    return []
                return [float(x.strip()) for x in cleaned.split(",")]
            return list(value)

        return process


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    point_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    document_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    token_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    start_page: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    end_page: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    page_numbers: Mapped[list[int]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
    )
    block_types: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=list,
    )
    embedding: Mapped[list[float]] = mapped_column(
        VectorType(384),
        nullable=False,
    )
    client_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    tenant_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    patient_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    source_system: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    collection_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    owner_subject_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    document_type: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    file_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<DocumentChunk doc={self.document_id} idx={self.chunk_index} point={self.point_id}>"
