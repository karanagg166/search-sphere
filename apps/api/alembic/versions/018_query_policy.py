"""Server-owned client query routing policy; no implicit general/tools access."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "018_query_policy"
down_revision = "017_conversation_scope"
branch_labels = depends_on = None


def upgrade():
    op.create_table("query_policies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("general_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("semantic_routing_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("allowed_tools", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.UniqueConstraint("client_id", "tenant_id", name="uq_query_policy_client_tenant"))
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE query_policies ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("query_policies")
