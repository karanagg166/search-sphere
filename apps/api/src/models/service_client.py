import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class ServiceClient(Base):
    """
    Authoritative entity representing an external registered application or client service.

    Provides independent authentication credentials (hashed tokens), tenant boundaries,
    and scoped access permissions for microservices (Quick-Clinic, ExamArena, etc.).
    """

    __tablename__ = "service_clients"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    client_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    api_key_hash: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    api_key_prefix: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        index=True,
    )

    allowed_scopes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

    authorized_tenants: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: ["*"],
    )

    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=120,
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

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    def is_active(self) -> bool:
        return self.status == "active" and self.revoked_at is None

    def is_tenant_authorized(self, tenant_id: str) -> bool:
        if not self.authorized_tenants:
            return False
        if "*" in self.authorized_tenants:
            return True
        return tenant_id in self.authorized_tenants

    def has_scope(self, scope: str) -> bool:
        if not self.allowed_scopes:
            return False
        if "*" in self.allowed_scopes:
            return True
        return scope in self.allowed_scopes

    def __repr__(self) -> str:
        return f"<ServiceClient client_id={self.client_id} status={self.status}>"
