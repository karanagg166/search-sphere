"""Reuse conversations/messages with distinct delegated actor and evidence scopes."""

import sqlalchemy as sa

from alembic import op

revision = "017_conversation_scope"
down_revision = "016_document_evidence"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("conversations") as batch:
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=True)
        for name, length in [("client_id", 64), ("tenant_id", 128), ("actor_id", 255),
                             ("collection_id", 128), ("subject_id", 255), ("external_reference", 255)]:
            batch.add_column(sa.Column(name, sa.String(length)))
        batch.create_check_constraint("ck_conversation_identity",
            "(user_id IS NOT NULL AND client_id IS NULL AND tenant_id IS NULL AND actor_id IS NULL AND external_reference IS NULL) OR "
            "(user_id IS NULL AND client_id IS NOT NULL AND tenant_id IS NOT NULL AND actor_id IS NOT NULL AND external_reference IS NOT NULL)")
    op.create_index("uq_delegated_conversation_reference", "conversations", [
        "client_id", "tenant_id", "actor_id", sa.text("coalesce(collection_id, '')"),
        sa.text("coalesce(subject_id, '')"), "external_reference",
    ], unique=True)


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM conversations WHERE user_id IS NULL)")).scalar():
        raise RuntimeError("Export delegated conversations before reverting their schema; downgrade will not erase messages.")
    op.drop_index("uq_delegated_conversation_reference", table_name="conversations")
    with op.batch_alter_table("conversations") as batch:
        batch.drop_constraint("ck_conversation_identity", type_="check")
        for name in ["external_reference", "subject_id", "collection_id", "actor_id", "tenant_id", "client_id"]:
            batch.drop_column(name)
        batch.alter_column("user_id", existing_type=sa.String(36), nullable=False)
