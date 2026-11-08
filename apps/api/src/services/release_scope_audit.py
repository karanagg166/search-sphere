"""Read-only proof for legacy synthetic receipts; never selects document contents."""

import hashlib
import hmac

from sqlalchemy import text

from src.services.release_identity import RELEASE_EMAIL, RELEASE_USER_ID


class ReleaseScopeNotEmptyError(ValueError):
    """A safe stage label, never SQL parameters or stored record contents."""


async def audit_native_release_scope(db, fixture_key):
    """Require the signed dedicated owner and zero outstanding owned resources.

    The caller serializes release runs and holds a repeatable-read, read-only
    transaction. Read Supabase's private object catalog using exact scoped prefixes.
    No object, source, message, credential or hash-field contents are returned.
    """
    if not isinstance(fixture_key, str) or len(fixture_key.encode()) < 32:
        raise ReleaseScopeNotEmptyError("fixture_key_required")
    marker = hmac.new(fixture_key.encode(), b"search-sphere-release-identity-v1", hashlib.sha256).hexdigest()
    params = {"owner": RELEASE_USER_ID, "email": RELEASE_EMAIL,
        "name": "Synthetic release " + marker[:24], "bucket": "documents",
        "prefix": "documents/" + RELEASE_USER_ID + "/%"}
    owned = await db.scalar(text("""SELECT count(*) FROM users
        WHERE id=:owner AND email=:email AND name=:name
        AND auth_provider='release_synthetic' AND is_active IS TRUE"""), params)
    if owned != 1:
        raise ReleaseScopeNotEmptyError("synthetic_owner_unverified")
    scope = "client_id='search_sphere' AND tenant_id=:owner"
    parent_ids = """SELECT id FROM documents WHERE user_id=:owner UNION
        SELECT document_id FROM document_deletions WHERE kind='standalone'
        AND client_id='search_sphere' AND tenant_id=:owner"""
    checks = {
        "documents": "SELECT count(*) FROM documents WHERE user_id=:owner",
        "conversations": "SELECT count(*) FROM conversations WHERE user_id=:owner",
        "vectors": "SELECT count(*) FROM document_chunks WHERE client_id IS NULL AND source_system IS NULL AND document_id IN (" + parent_ids + ")",
        "active_jobs": "SELECT count(*) FROM ingestion_jobs WHERE document_kind='standalone' AND status IN ('QUEUED','RUNNING','RETRY') AND document_id IN (" + parent_ids + ")",
        "pending_objects": "SELECT count(*) FROM document_deletions WHERE " + scope + " AND (status!='COMPLETE' OR object_deleted IS NOT TRUE)",
        "structured_evidence": "SELECT count(*) FROM document_evidence WHERE " + scope,
        "published_versions": "SELECT count(*) FROM document_versions WHERE " + scope,
    }
    counts = {}
    for label, query in checks.items():
        count = await db.scalar(text(query), params)
        if type(count) is not int or count != 0:
            raise ReleaseScopeNotEmptyError(label + "_remain")
        counts[label] = count
    private_bucket = await db.scalar(text("SELECT count(*) FROM storage.buckets WHERE id=:bucket AND public IS FALSE"), params)
    if private_bucket != 1:
        raise ReleaseScopeNotEmptyError("private_bucket_unverified")
    storage_count = await db.scalar(text("""SELECT count(*) FROM storage.objects o WHERE o.bucket_id=:bucket
        AND (o.name LIKE :prefix OR EXISTS (SELECT 1 FROM document_deletions d
            WHERE d.client_id='search_sphere' AND d.tenant_id=:owner
            AND (o.name=d.storage_key OR o.name LIKE 'derived/standalone/' || d.source_record_id || '/%')))"""), params)
    if type(storage_count) is not int or storage_count != 0:
        raise ReleaseScopeNotEmptyError("private_objects_remain")
    return {"owner": RELEASE_USER_ID, "counts": {**counts, "private_objects": storage_count}}
