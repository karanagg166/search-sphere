"""Add chunk_count to documents and external_documents.

Revision ID: 008_add_chunk_count
Revises: 007_medical_scope
Create Date: 2026-11-04 02:40:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "008_add_chunk_count"
down_revision: Union[str, Sequence[str], None] = "007_medical_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "documents" in tables:
        doc_cols = {col["name"] for col in inspector.get_columns("documents")}
        if "chunk_count" not in doc_cols:
            op.add_column("documents", sa.Column("chunk_count", sa.Integer(), nullable=True))

    if "external_documents" in tables:
        ext_cols = {col["name"] for col in inspector.get_columns("external_documents")}
        if "chunk_count" not in ext_cols:
            op.add_column("external_documents", sa.Column("chunk_count", sa.Integer(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "documents" in tables:
        doc_cols = {col["name"] for col in inspector.get_columns("documents")}
        if "chunk_count" in doc_cols:
            op.drop_column("documents", "chunk_count")

    if "external_documents" in tables:
        ext_cols = {col["name"] for col in inspector.get_columns("external_documents")}
        if "chunk_count" in ext_cols:
            op.drop_column("external_documents", "chunk_count")
