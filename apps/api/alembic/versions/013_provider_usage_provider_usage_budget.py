"""provider_usage_budget

Revision ID: 013_provider_usage
Revises: 012_ingestion_jobs
Create Date: 2026-11-05 20:51:22.332819

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "013_provider_usage"
down_revision: str | Sequence[str] | None = "012_ingestion_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_usage",
        sa.Column("bucket", sa.String(160), primary_key=True),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("used >= 0", name="ck_provider_usage_nonnegative"),
    )
    op.create_index("ix_provider_usage_expires_at", "provider_usage", ["expires_at"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE provider_usage ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.drop_table("provider_usage")
