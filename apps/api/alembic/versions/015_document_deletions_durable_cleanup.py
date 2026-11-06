"""Durable document tombstones and private-object cleanup receipts."""

import sqlalchemy as sa
from alembic import op

revision = "015_document_deletions"
down_revision = "014_inference_data_policy"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "document_deletions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("document_id", sa.String(128), nullable=False),
        sa.Column("source_record_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collection_id", sa.String(128)),
        sa.Column("subject_id", sa.String(128)),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("object_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("kind", "client_id", "tenant_id", "document_id", name="uq_document_deletion_identity"),
    )
    op.create_index("ix_document_deletions_status", "document_deletions", ["status"])
    op.create_index("ix_document_deletions_available_at", "document_deletions", ["available_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE document_deletions ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("document_deletions")
