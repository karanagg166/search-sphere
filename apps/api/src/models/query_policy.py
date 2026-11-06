import uuid

from sqlalchemy import JSON, Boolean, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class QueryPolicy(Base):
    __tablename__ = "query_policies"
    __table_args__ = (UniqueConstraint("client_id", "tenant_id", name="uq_query_policy_client_tenant"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    client_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    general_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    semantic_routing_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list)
