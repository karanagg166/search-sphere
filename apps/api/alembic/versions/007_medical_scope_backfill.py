"""Normalize the medical compatibility adapter's generic metadata (vectors separate).

Revision ID: 007_medical_scope
Revises: 006_scope_external_doc
"""
import hashlib
import uuid

from alembic import op
import sqlalchemy as sa

revision = "007_medical_scope"
down_revision = "006_scope_external_doc"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "tenant_id" not in {column["name"] for column in sa.inspect(bind).get_columns("medical_observations")}:
        op.add_column("medical_observations", sa.Column("tenant_id", sa.String(128), nullable=False, server_default="quick_clinic_default"))
        op.create_index("ix_medical_observations_tenant_id", "medical_observations", ["tenant_id"])
    rows = bind.execute(sa.text("SELECT id, tenant_id, external_patient_id, owner_subject_id, collection_id FROM external_documents WHERE source_system = 'quick_clinic'")).mappings().all()
    for row in rows:
        patient = row["external_patient_id"]
        if not patient or patient == "default":
            continue
        tenant = "quick_clinic_default" if row["tenant_id"] == "default" else row["tenant_id"]
        collection = row["collection_id"]
        if collection is None or collection == patient:
            collection = f"patient_{hashlib.sha256(patient.encode()).hexdigest()[:32]}_records"
        bind.execute(sa.text("UPDATE external_documents SET tenant_id=:tenant, owner_subject_id=:owner, collection_id=:collection WHERE id=:id"), {"tenant": tenant, "owner": row["owner_subject_id"] or patient, "collection": collection, "id": row["id"]})
        exists = bind.execute(sa.text("SELECT id FROM document_collections WHERE client_id='quick_clinic' AND tenant_id=:tenant AND collection_id=:collection"), {"tenant": tenant, "collection": collection}).first()
        if not exists:
            bind.execute(sa.text("INSERT INTO document_collections (id, client_id, tenant_id, collection_id, name) VALUES (:id, 'quick_clinic', :tenant, :collection, 'Medical records')"), {"id": str(uuid.uuid4()), "tenant": tenant, "collection": collection})


def downgrade():
    # Scope metadata is security relevant and cannot safely be discarded.
    pass
