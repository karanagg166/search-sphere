"""restrict_data_api_surface

Revision ID: 011_private_data_api_surface
Revises: 010_backend_data_api_lockdown
Create Date: 2026-11-05 20:27:36.816794

"""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "011_private_data_api_surface"
down_revision: str | Sequence[str] | None = "010_backend_data_api_lockdown"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        path = Path(__file__).parents[1] / "sql" / "private_data_api_surface.sql"
        op.get_bind().execute(text(path.read_text()))
        op.execute("NOTIFY pgrst, 'reload config'")
        op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    raise RuntimeError(
        "Restore an exposed schema only after explicit dependency and access review."
    )
