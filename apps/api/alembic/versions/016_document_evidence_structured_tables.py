"""Full structured table evidence, separate from retrieval chunks."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "016_document_evidence"
down_revision = "015_document_deletions"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "document_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_kind", sa.String(16), nullable=False),
        sa.Column("source_record_id", sa.String(36), nullable=False),
        sa.Column("evidence_id", sa.String(64), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collection_id", sa.String(128)),
        sa.Column("subject_id", sa.String(128)),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("data_json", sa.JSON().with_variant(JSONB, "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_kind", "source_record_id", "evidence_id", name="uq_document_evidence_source"),
    )
    op.create_index("ix_document_evidence_source_record_id", "document_evidence", ["source_record_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE document_evidence ENABLE ROW LEVEL SECURITY")


def downgrade():
    op.drop_table("document_evidence")
