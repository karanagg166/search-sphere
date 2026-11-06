"""inference_data_policy

Revision ID: 014_inference_data_policy
Revises: 013_provider_usage
Create Date: 2026-11-06 11:13:41.557080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '014_inference_data_policy'
down_revision: Union[str, Sequence[str], None] = '013_provider_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "inference_data_policies",
        sa.Column("client_id", sa.String(64), primary_key=True),
        sa.Column("tenant_id", sa.String(128), primary_key=True),
        sa.Column("collection_scope", sa.String(128), primary_key=True, server_default=""),
        sa.Column("subject_scope", sa.String(128), primary_key=True, server_default=""),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("approval_ref", sa.String(255), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("classification IN ('public','synthetic','confidential','medical')", name="ck_inference_classification"),
        sa.CheckConstraint("length(trim(approval_ref)) > 0", name="ck_inference_approval_ref"),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE inference_data_policies ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("inference_data_policies")
