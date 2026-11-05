"""lock_down_backend_data_api

Revision ID: 010_backend_data_api_lockdown
Revises: 009_add_pgvector_chunks
Create Date: 2026-11-05 19:53:14.377678

"""

from collections.abc import Sequence
from pathlib import Path

from sqlalchemy import text

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "010_backend_data_api_lockdown"
down_revision: str | Sequence[str] | None = "009_add_pgvector_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove unintended Data API access without changing backend SQL or Auth."""
    if op.get_bind().dialect.name == "postgresql":
        sql_path = Path(__file__).parents[1] / "sql" / "backend_data_api_lockdown.sql"
        sql = sql_path.read_text()
        # asyncpg prepares one statement at a time; keep each DO block intact.
        for block in ("lockdown", "storage"):
            start = sql.index(f"DO ${block}$")
            end = sql.index(f"${block}$;", start) + len(f"${block}$;")
            op.get_bind().execute(text(sql[start:end]))
        op.execute("NOTIFY pgrst, 'reload schema'")


def downgrade() -> None:
    raise RuntimeError(
        "Security lock-down is irreversible; restore only reviewed explicit grants."
    )
