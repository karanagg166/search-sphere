"""Read-only metadata proof against real PostgreSQL/pgvector; synthetic rows only."""
import uuid

import pytest
import pytest_asyncio
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import event, text
from src.models.conversation import Conversation
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence
from src.models.document_version import DocumentVersion
from src.models.ingestion_job import IngestionJob
from src.services.release_identity import (
    RELEASE_EMAIL,
    RELEASE_USER_ID,
    provision_release_identity,
)
from src.services.release_scope_audit import (
    ReleaseScopeNotEmptyError,
    audit_native_release_scope,
)

KEY = "synthetic-native-scope-audit-key-only-32-bytes"


@pytest_asyncio.fixture
async def scope(fixture):
    async with fixture.sessions() as db:
        transaction = await db.begin()
        try:
            await provision_release_identity(db, RELEASE_EMAIL, "synthetic-release-password-32-bytes-long", KEY)
            await db.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
            await db.execute(text("CREATE TABLE storage.buckets (id text PRIMARY KEY, public boolean)"))
            await db.execute(text("CREATE TABLE storage.objects (name text, bucket_id text)"))
            await db.execute(text("INSERT INTO storage.buckets VALUES ('documents', false), ('unrelated',true)"))
            yield db
        finally:
            await transaction.rollback()


async def tombstone(db):
    value = str(uuid.uuid4())
    db.add(DocumentDeletion(kind="standalone", document_id=value, source_record_id=value,
        client_id="search_sphere", tenant_id=RELEASE_USER_ID, subject_id=RELEASE_USER_ID,
        storage_key=f"documents/{RELEASE_USER_ID}/{value}.pdf", status="COMPLETE", object_deleted=True))
    await db.flush()
    return value


@pytest.mark.asyncio
async def test_empty_owned_scope_returns_only_zero_counts_and_issues_only_selects(scope):
    statements = []
    connection = await scope.connection()
    listener = lambda conn, cursor, statement, parameters, context, many: statements.append(statement)
    event.listen(connection.sync_connection, "before_cursor_execute", listener)
    try:
        result = await audit_native_release_scope(scope, KEY)
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", listener)
    assert result["owner"] == RELEASE_USER_ID
    assert len(result["counts"]) == 8 and set(result["counts"].values()) == {0}
    assert statements and all(value.lstrip().upper().startswith("SELECT") for value in statements)
    assert "hashed_password" not in " ".join(statements) and "content" not in " ".join(statements)


@pytest.mark.asyncio
async def test_changed_fixture_key_or_revoked_owner_cannot_approve_reconciliation(scope):
    with pytest.raises(ReleaseScopeNotEmptyError, match="synthetic_owner_unverified"):
        await audit_native_release_scope(scope, KEY + "changed")
    await scope.execute(text("UPDATE users SET is_active=false WHERE id=:owner"), {"owner": RELEASE_USER_ID})
    with pytest.raises(ReleaseScopeNotEmptyError, match="synthetic_owner_unverified"):
        await audit_native_release_scope(scope, KEY)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["document", "conversation", "job", "vectors", "pending-object", "structured", "version"])
async def test_outstanding_owned_resources_block_reconciliation(scope, kind):
    value = await tombstone(scope)
    label = {"document": "documents", "conversation": "conversations", "job": "active_jobs",
        "vectors": "vectors", "pending-object": "pending_objects", "structured": "structured_evidence",
        "version": "published_versions"}[kind]
    if kind == "document":
        scope.add(Document(id=str(uuid.uuid4()), user_id=RELEASE_USER_ID, filename="synthetic.pdf",
            storage_key="documents/synthetic-new.pdf", file_url="private", file_size=1))
    elif kind == "conversation":
        scope.add(Conversation(user_id=RELEASE_USER_ID, title="Synthetic fixture"))
    elif kind == "job":
        scope.add(IngestionJob(document_kind="standalone", document_id=value, status="RUNNING"))
    elif kind == "vectors":
        scope.add(DocumentChunk(point_id=value, document_id=value, chunk_index=0,
            content="PUBLIC SYNTHETIC", embedding=[1.0] + [0.0] * 383))
    elif kind == "structured":
        scope.add(DocumentEvidence(document_kind="standalone", source_record_id=value, evidence_id=value,
            client_id="search_sphere", tenant_id=RELEASE_USER_ID, source_sha256="a" * 64,
            kind="table", data_json={"synthetic": True}))
    elif kind == "version":
        scope.add(DocumentVersion(document_kind="standalone", source_record_id=value, number=1,
            job_id=str(uuid.uuid4()), generation=1, client_id="search_sphere", tenant_id=RELEASE_USER_ID,
            storage_key="synthetic-original", mime_type="application/pdf", file_size=1,
            embedding_model="synthetic-model", chunk_count=1))
    else:
        await scope.execute(text("UPDATE document_deletions SET object_deleted=false WHERE document_id=:id"), {"id": value})
    await scope.flush()
    with pytest.raises(ReleaseScopeNotEmptyError, match=label + "_remain"):
        await audit_native_release_scope(scope, KEY)


@pytest.mark.asyncio
async def test_public_bucket_or_orphan_private_original_cannot_pass(scope):
    await scope.execute(text("UPDATE storage.buckets SET public=true WHERE id='documents'"))
    with pytest.raises(ReleaseScopeNotEmptyError, match="private_bucket_unverified"):
        await audit_native_release_scope(scope, KEY)
    await scope.execute(text("UPDATE storage.buckets SET public=false WHERE id='documents'"))
    await scope.execute(text("INSERT INTO storage.objects VALUES (:name,'documents')"),
        {"name": f"documents/{RELEASE_USER_ID}/unregistered-synthetic.pdf"})
    with pytest.raises(ReleaseScopeNotEmptyError, match="private_objects_remain"):
        await audit_native_release_scope(scope, KEY)


@pytest.mark.asyncio
async def test_derived_object_for_a_completed_tombstone_is_checked(scope):
    value = await tombstone(scope)
    await scope.execute(text("INSERT INTO storage.objects VALUES (:name,'documents')"),
        {"name": f"derived/standalone/{value}/synthetic-crop.png"})
    with pytest.raises(ReleaseScopeNotEmptyError, match="private_objects_remain"):
        await audit_native_release_scope(scope, KEY)


@pytest.mark.asyncio
async def test_unrelated_private_objects_are_not_read_or_reclassified(scope):
    await scope.execute(text("INSERT INTO storage.objects VALUES ('documents/foreign/synthetic.pdf','documents')"))
    result = await audit_native_release_scope(scope, KEY)
    assert set(result["counts"].values()) == {0}
    assert await scope.scalar(text("SELECT count(*) FROM storage.objects")) == 1
