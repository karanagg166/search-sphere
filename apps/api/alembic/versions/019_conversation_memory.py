"""Bounded compact memory separate from message text; conversation owns scope."""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "019_conversation_memory"
down_revision = "018_query_policy"
branch_labels = depends_on = None


def upgrade():
    op.create_table("conversation_memory",
        sa.Column("conversation_id",sa.String(36),sa.ForeignKey("conversations.id",ondelete="CASCADE"),primary_key=True),
        sa.Column("state",sa.JSON().with_variant(JSONB,"postgresql"),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()))
    if op.get_bind().dialect.name=="postgresql":
        op.execute("ALTER TABLE conversation_memory ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("conversation_memory")
