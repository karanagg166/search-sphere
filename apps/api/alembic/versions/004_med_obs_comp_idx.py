"""004_med_obs_comp_idx

Revision ID: 004_med_obs_comp_idx
Revises: 003_medical_observations
Create Date: 2026-10-05 02:25:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "004_med_obs_comp_idx"
down_revision: Union[str, Sequence[str], None] = "003_medical_observations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes("medical_observations")]

    if "ix_med_obs_src_pat_type_date" not in indexes:
        op.create_index(
            "ix_med_obs_src_pat_type_date",
            "medical_observations",
            ["source_system", "external_patient_id", "observation_type", "observed_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = [idx["name"] for idx in inspector.get_indexes("medical_observations")]

    if "ix_med_obs_src_pat_type_date" in indexes:
        op.drop_index("ix_med_obs_src_pat_type_date", table_name="medical_observations")
