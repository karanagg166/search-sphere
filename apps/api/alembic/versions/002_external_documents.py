"""002_external_documents

Revision ID: 002_external_documents
Revises: 001_initial_schema
Create Date: 2026-10-04 04:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002_external_documents"
down_revision: Union[str, Sequence[str], None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. External Documents table
    if "external_documents" not in tables:
        op.create_table(
            "external_documents",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("source_system", sa.String(length=50), nullable=False, server_default="quick_clinic"),
            sa.Column("external_document_id", sa.String(length=128), nullable=False),
            sa.Column("external_patient_id", sa.String(length=128), nullable=False),
            sa.Column("storage_path", sa.String(length=512), nullable=False),
            sa.Column("file_name", sa.String(length=255), nullable=False),
            sa.Column("mime_type", sa.String(length=100), nullable=False),
            sa.Column("file_size", sa.BigInteger(), nullable=False),
            sa.Column("document_type", sa.String(length=50), nullable=False),
            sa.Column("report_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=False, server_default="QUEUED"),
            sa.Column("processing_error", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("source_system", "external_document_id", name="uq_external_documents_source_doc_id"),
        )
        op.create_index(op.f("ix_external_documents_id"), "external_documents", ["id"], unique=False)
        op.create_index(op.f("ix_external_documents_source_system"), "external_documents", ["source_system"], unique=False)
        op.create_index(op.f("ix_external_documents_external_document_id"), "external_documents", ["external_document_id"], unique=False)
        op.create_index(op.f("ix_external_documents_external_patient_id"), "external_documents", ["external_patient_id"], unique=False)
        op.create_index(op.f("ix_external_documents_status"), "external_documents", ["status"], unique=False)

    # 2. External Document Contents table
    if "external_document_contents" not in tables:
        op.create_table(
            "external_document_contents",
            sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
            sa.Column("external_document_id", sa.String(length=36), sa.ForeignKey("external_documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("raw_text", sa.Text(), nullable=False),
            sa.Column("character_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index(op.f("ix_external_document_contents_external_document_id"), "external_document_contents", ["external_document_id"], unique=True)


def downgrade() -> None:
    op.drop_table("external_document_contents")
    op.drop_table("external_documents")
