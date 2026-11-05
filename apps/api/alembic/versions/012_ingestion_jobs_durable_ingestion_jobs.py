"""durable_ingestion_jobs

Revision ID: 012_ingestion_jobs
Revises: 011_private_data_api_surface
Create Date: 2026-11-05 20:33:35.157500

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "012_ingestion_jobs"
down_revision: str | Sequence[str] | None = "011_private_data_api_surface"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_kind", sa.String(16), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("stage", sa.String(32), nullable=False, server_default="registered"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "document_kind", "document_id", name="uq_ingestion_job_document"
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="ck_ingestion_attempts"
        ),
        sa.CheckConstraint(
            "document_kind IN ('standalone','external')", name="ck_ingestion_kind"
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','RETRY','SUCCEEDED','FAILED')",
            name="ck_ingestion_status",
        ),
    )
    op.create_index(
        "ix_ingestion_jobs_claim",
        "ingestion_jobs",
        ["status", "available_at", "created_at"],
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE ingestion_jobs ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("ingestion_jobs")
