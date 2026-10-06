"""Real Alembic migration test, runnable when the migration dependency is installed."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("alembic.command")

from src.security.medical_context import patient_collection_id


def test_existing_medical_metadata_is_backfilled(tmp_path):
    database = tmp_path / "migration.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{database}"}
    cwd = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "006_scope_external_doc"], cwd=cwd, env=env, check=True, capture_output=True)
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO external_documents (id, source_system, external_document_id, external_patient_id, tenant_id, storage_path, file_name, mime_type, file_size, document_type, status) VALUES ('synthetic-row', 'quick_clinic', 'synthetic-doc', 'synthetic-patient', 'default', 'synthetic/path', 'synthetic.pdf', 'application/pdf', 100, 'LAB_REPORT', 'READY')")
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=cwd, env=env, check=True, capture_output=True)
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT tenant_id, owner_subject_id, collection_id FROM external_documents WHERE id='synthetic-row'").fetchone() == ("quick_clinic_default", "synthetic-patient", patient_collection_id("synthetic-patient"))
        assert conn.execute("SELECT count(*) FROM document_collections WHERE client_id='quick_clinic'").fetchone()[0] == 1
        assert "tenant_id" in {column[1] for column in conn.execute("PRAGMA table_info(medical_observations)")}
