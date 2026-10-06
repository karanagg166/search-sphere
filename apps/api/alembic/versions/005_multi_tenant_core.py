"""005_multi_tenant_core

Revision ID: 005_multi_tenant_core
Revises: 004_med_obs_comp_idx
Create Date: 2026-10-06 12:00:00.000000

"""
import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "005_multi_tenant_core"
down_revision: Union[str, Sequence[str], None] = "004_med_obs_comp_idx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    # 1. Service Clients table
    if "service_clients" not in tables:
        op.create_table(
            "service_clients",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("client_id", sa.String(length=64), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("api_key_hash", sa.String(length=128), nullable=False),
            sa.Column("api_key_prefix", sa.String(length=16), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
            sa.Column("allowed_scopes", sa.JSON(), nullable=False),
            sa.Column("authorized_tenants", sa.JSON(), nullable=False),
            sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="120"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(op.f("ix_service_clients_client_id"), "service_clients", ["client_id"], unique=True)
        op.create_index(op.f("ix_service_clients_api_key_hash"), "service_clients", ["api_key_hash"], unique=False)
        op.create_index(op.f("ix_service_clients_status"), "service_clients", ["status"], unique=False)

    # 2. Document Collections table
    if "document_collections" not in tables:
        op.create_table(
            "document_collections",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("client_id", sa.String(length=64), nullable=False),
            sa.Column("tenant_id", sa.String(length=128), nullable=False),
            sa.Column("collection_id", sa.String(length=128), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("client_id", "tenant_id", "collection_id", name="uq_collections_client_tenant_collection"),
        )
        op.create_index(op.f("ix_document_collections_client_id"), "document_collections", ["client_id"], unique=False)
        op.create_index(op.f("ix_document_collections_tenant_id"), "document_collections", ["tenant_id"], unique=False)
        op.create_index(op.f("ix_document_collections_collection_id"), "document_collections", ["collection_id"], unique=False)

    # 3. Add columns to external_documents if present
    if "external_documents" in tables:
        ext_cols = [c["name"] for c in inspector.get_columns("external_documents")]
        if "tenant_id" not in ext_cols:
            op.add_column(
                "external_documents",
                sa.Column("tenant_id", sa.String(length=128), nullable=False, server_default="default"),
            )
            op.create_index(op.f("ix_external_documents_tenant_id"), "external_documents", ["tenant_id"], unique=False)

        if "collection_id" not in ext_cols:
            op.add_column(
                "external_documents",
                sa.Column("collection_id", sa.String(length=128), nullable=True),
            )
            op.create_index(op.f("ix_external_documents_collection_id"), "external_documents", ["collection_id"], unique=False)

        if "owner_subject_id" not in ext_cols:
            op.add_column(
                "external_documents",
                sa.Column("owner_subject_id", sa.String(length=128), nullable=True),
            )
            op.create_index(op.f("ix_external_documents_owner_subject_id"), "external_documents", ["owner_subject_id"], unique=False)

        if "metadata_json" not in ext_cols:
            op.add_column(
                "external_documents",
                sa.Column("metadata_json", sa.JSON(), nullable=True),
            )

        # Backfill existing Quick Clinic records
        try:
            op.execute(
                """
                UPDATE external_documents
                SET tenant_id = 'quick_clinic_default',
                    owner_subject_id = external_patient_id,
                    collection_id = external_patient_id
                WHERE source_system = 'quick_clinic'
                  AND (owner_subject_id IS NULL OR tenant_id = 'default')
                """
            )
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "external_documents" in tables:
        ext_cols = [c["name"] for c in inspector.get_columns("external_documents")]
        if "metadata_json" in ext_cols:
            op.drop_column("external_documents", "metadata_json")
        if "owner_subject_id" in ext_cols:
            op.drop_index(op.f("ix_external_documents_owner_subject_id"), table_name="external_documents")
            op.drop_column("external_documents", "owner_subject_id")
        if "collection_id" in ext_cols:
            op.drop_index(op.f("ix_external_documents_collection_id"), table_name="external_documents")
            op.drop_column("external_documents", "collection_id")
        if "tenant_id" in ext_cols:
            op.drop_index(op.f("ix_external_documents_tenant_id"), table_name="external_documents")
            op.drop_column("external_documents", "tenant_id")

    if "document_collections" in tables:
        op.drop_table("document_collections")

    if "service_clients" in tables:
        op.drop_table("service_clients")
