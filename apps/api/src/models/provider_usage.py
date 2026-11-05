from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ProviderUsage(Base):
    """Metadata-only durable quota windows shared across processes and restarts."""

    __tablename__ = "provider_usage"

    bucket: Mapped[str] = mapped_column(String(160), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
