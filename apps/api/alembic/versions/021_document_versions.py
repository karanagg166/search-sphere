"""Atomic published-version identity and original-byte provenance."""

import sqlalchemy as sa

from alembic import op

revision = "021_document_versions"
down_revision = "020_retrieval_cache"
branch_labels = depends_on = None


def upgrade():
    op.create_table("document_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_kind", sa.String(16), nullable=False),
        sa.Column("source_record_id", sa.String(36), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("collection_id", sa.String(128)), sa.Column("subject_id", sa.String(128)),
        sa.Column("storage_key", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False), sa.Column("source_sha256", sa.String(64)),
        sa.Column("embedding_model", sa.String(128), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_kind", "source_record_id", "number", name="uq_document_version_number"),
        sa.UniqueConstraint("job_id", "generation", name="uq_document_version_delivery"))
    op.create_index("ix_document_versions_source_record_id", "document_versions", ["source_record_id"])
    for table in ["documents", "external_documents"]:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("current_version_id", sa.String(36), nullable=True))
            batch.create_foreign_key("fk_" + table + "_current_version", "document_versions", ["current_version_id"], ["id"], ondelete="SET NULL")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE document_versions ENABLE ROW LEVEL SECURITY")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM document_versions")).scalar_one():
        raise RuntimeError("Cannot discard published version provenance; archive explicitly before downgrading.")
    for table in ["documents", "external_documents"]:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint("fk_" + table + "_current_version", type_="foreignkey")
            batch.drop_column("current_version_id")
    op.drop_table("document_versions")
