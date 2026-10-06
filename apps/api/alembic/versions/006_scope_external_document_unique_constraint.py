"""006_scope_external_document_unique_constraint

Revision ID: 006_scope_external_doc
Revises: 005_multi_tenant_core
Create Date: 2026-10-06 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "006_scope_external_doc"
down_revision: Union[str, Sequence[str], None] = "005_multi_tenant_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    dialect = bind.dialect.name

    # Handle Postgres / SQLite differences
    if dialect == "postgresql":
        # Drop legacy two-column unique constraint
        op.execute("ALTER TABLE external_documents DROP CONSTRAINT IF EXISTS uq_external_documents_source_doc_id")
        # Add new tenant-scoped three-column unique constraint
        op.create_unique_constraint(
            "uq_external_documents_tenant_doc_id",
            "external_documents",
            ["source_system", "tenant_id", "external_document_id"],
        )
    else:
        # SQLite or others: create unique index
        try:
            op.create_index(
                "uq_external_documents_tenant_doc_id",
                "external_documents",
                ["source_system", "tenant_id", "external_document_id"],
                unique=True,
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "postgresql":
        op.execute("ALTER TABLE external_documents DROP CONSTRAINT IF EXISTS uq_external_documents_tenant_doc_id")
        op.create_unique_constraint(
            "uq_external_documents_source_doc_id",
            "external_documents",
            ["source_system", "external_document_id"],
        )
    else:
        try:
            op.drop_index("uq_external_documents_tenant_doc_id", table_name="external_documents")
        except Exception:
            pass
