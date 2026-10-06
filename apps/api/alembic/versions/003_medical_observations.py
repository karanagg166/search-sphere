"""003_medical_observations

Revision ID: 003_medical_observations
Revises: 002_external_documents
Create Date: 2026-10-05 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "003_medical_observations"
down_revision: Union[str, Sequence[str], None] = "002_external_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = inspector.get_table_names()

    if "medical_observations" not in tables:
        op.create_table(
            "medical_observations",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("source_system", sa.String(length=50), nullable=False, server_default="quick_clinic"),
            sa.Column("external_patient_id", sa.String(length=128), nullable=False),
            sa.Column("external_document_id", sa.String(length=128), nullable=False),
            sa.Column("observation_type", sa.String(length=50), nullable=False),
            sa.Column("display_name", sa.String(length=100), nullable=False),
            sa.Column("value_numeric", sa.Float(), nullable=True),
            sa.Column("value_text", sa.String(length=255), nullable=True),
            sa.Column("value_secondary_numeric", sa.Float(), nullable=True),
            sa.Column("unit", sa.String(length=50), nullable=True),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_date_inferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("page_number", sa.Integer(), nullable=True),
            sa.Column("chunk_index", sa.Integer(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
            sa.Column("extraction_method", sa.String(length=50), nullable=False, server_default="REGEX"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )

        op.create_index(op.f("ix_medical_observations_id"), "medical_observations", ["id"], unique=False)
        op.create_index(op.f("ix_medical_observations_source_system"), "medical_observations", ["source_system"], unique=False)
        op.create_index(op.f("ix_medical_observations_external_patient_id"), "medical_observations", ["external_patient_id"], unique=False)
        op.create_index(op.f("ix_medical_observations_external_document_id"), "medical_observations", ["external_document_id"], unique=False)
        op.create_index(op.f("ix_medical_observations_observation_type"), "medical_observations", ["observation_type"], unique=False)
        op.create_index(op.f("ix_medical_observations_observed_at"), "medical_observations", ["observed_at"], unique=False)

        # Composite indexes for fast patient-bounded query filtering
        op.create_index(
            "ix_med_obs_src_pat_type",
            "medical_observations",
            ["source_system", "external_patient_id", "observation_type"],
            unique=False,
        )
        op.create_index(
            "ix_med_obs_src_pat_date",
            "medical_observations",
            ["source_system", "external_patient_id", "observed_at"],
            unique=False,
        )
        op.create_index(
            "ix_med_obs_src_doc",
            "medical_observations",
            ["source_system", "external_document_id"],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("medical_observations")
