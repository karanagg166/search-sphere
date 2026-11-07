"""Prevent deleted delegated references from being registered again."""

import sqlalchemy as sa

from alembic import op

revision = "022_conversation_deletions"
down_revision = "021_document_versions"
branch_labels = depends_on = None


def upgrade():
    op.create_table("conversation_deletions",
        sa.Column("reference_hash", sa.String(64), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_conversation_deletions_client_id", "conversation_deletions", ["client_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE conversation_deletions ENABLE ROW LEVEL SECURITY")
        op.execute("REVOKE ALL ON TABLE conversation_deletions FROM PUBLIC")
        for role in ("anon", "authenticated"):
            if op.get_bind().execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname=:role"), {"role": role}).scalar():
                op.execute(f"REVOKE ALL ON TABLE conversation_deletions FROM {role}")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM conversation_deletions")).scalar_one():
        raise RuntimeError("Cannot discard conversation deletion fences; preserve them before downgrading.")
    op.drop_table("conversation_deletions")
