from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class InferenceDataPolicy(Base):
    """Operator-owned classification approval; consuming APIs have no write route."""

    __tablename__ = "inference_data_policies"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    collection_scope: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    subject_scope: Mapped[str] = mapped_column(String(128), primary_key=True, default="")
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    approval_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
