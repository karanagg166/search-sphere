"""Private bounded cache of retrieval references; no queries, passages or answers."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "020_retrieval_cache"
down_revision = "019_conversation_memory"
branch_labels = depends_on = None


def upgrade():
    op.create_table("retrieval_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("entries", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_retrieval_cache_expires_at", "retrieval_cache", ["expires_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE retrieval_cache ENABLE ROW LEVEL SECURITY")
        op.create_check_constraint("ck_retrieval_cache_bounded", "retrieval_cache", "octet_length(entries::text) <= 16384 AND jsonb_array_length(entries) <= 20")


def downgrade():
    op.drop_table("retrieval_cache")
