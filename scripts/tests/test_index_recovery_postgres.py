"""Real pgvector recovery with synthetic policies, originals and hosted adapters."""

import asyncio
import copy
import hashlib
import time

import pytest
from ingestion_test_support import approve, pdf, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import delete, select, update
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.ingestion_job import IngestionJob
from src.models.service_client import ServiceClient
from src.repositories.external_registration import register_external_document
from src.services.document_deletion import delete_registered_document
from src.services.index_recovery import (
    RecoveryScope,
    apply_recovery_plan,
    build_recovery_plan,
    digest,
)
from src.services.ingestion_worker import PostgresIngestionWorker
from test_query_access_postgres import indexed_document


def scope_for(fixture):
    return RecoveryScope("standalone", "search_sphere", fixture.owner_id, subject_id=fixture.owner_id)


async def missing(fixture):
    document, row = await indexed_document(fixture)
    async with fixture.sessions() as db, db.begin():
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
    return document, row


async def job_state(fixture, document_id):
    async with fixture.sessions() as db:
        row = (await db.execute(select(IngestionJob).where(IngestionJob.document_id == document_id))).scalar_one()
        return row.status, row.generation, row.attempts


@pytest.mark.asyncio
async def test_dry_run_has_no_mutations_provider_calls_or_sensitive_metadata(fixture):
    document, _ = await missing(fixture)
    prior = await job_state(fixture, document.id)
    calls = len(fixture.calls)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    assert [row["id"] for row in plan["entries"]] == [document.id]
    assert plan["entries"][0]["action"] == "enqueue"
    assert plan["entries"][0]["fingerprint"]
    assert len(fixture.calls) == calls and await job_state(fixture, document.id) == prior
    assert document.storage_key not in str(plan) and document.filename not in str(plan)
    assert "Interns are covered" not in str(plan)


@pytest.mark.asyncio
async def test_apply_restarts_one_job_and_worker_restores_usable_vectors(fixture):
    document, _ = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    result = await apply_recovery_plan(fixture.sessions, plan, digest(plan))
    assert result == [{"id": document.id, "result": "enqueued"}]
    assert await job_state(fixture, document.id) == ("QUEUED", 2, 0)
    assert await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage).run_once()
    assert await job_state(fixture, document.id) == ("SUCCEEDED", 2, 1)
    assert not (await build_recovery_plan(fixture.sessions, scope_for(fixture)))["entries"]


@pytest.mark.asyncio
async def test_duplicate_concurrent_apply_does_not_reset_generation_twice(fixture):
    document, _ = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    results = await asyncio.gather(*(apply_recovery_plan(fixture.sessions, plan, digest(plan)) for _ in range(2)))
    assert sum(row["result"] == "enqueued" for batch in results for row in batch) == 1
    assert await job_state(fixture, document.id) == ("QUEUED", 2, 0)


@pytest.mark.asyncio
async def test_other_owners_indexes_and_forged_receipt_id_cannot_be_changed(fixture):
    document, _ = await missing(fixture)
    other = RecoveryScope("standalone", "search_sphere", "00000000-0000-0000-0000-000000000001", subject_id="00000000-0000-0000-0000-000000000001")
    plan = await build_recovery_plan(fixture.sessions, other)
    assert plan["entries"] == []
    original = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    plan["entries"] = original["entries"]
    assert await apply_recovery_plan(fixture.sessions, plan, digest(plan)) == [{"id": document.id, "result": "source_unavailable"}]
    assert await job_state(fixture, document.id) == ("SUCCEEDED", 1, 1)


@pytest.mark.asyncio
async def test_policy_revocation_after_plan_fails_closed_before_enqueue(fixture):
    document, _ = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=False))
    assert (await apply_recovery_plan(fixture.sessions, plan, digest(plan)))[0]["result"] == "provider_not_approved"
    assert await job_state(fixture, document.id) == ("SUCCEEDED", 1, 1)
    assert (await build_recovery_plan(fixture.sessions, scope_for(fixture)))["entries"][0]["action"] == "provider_not_approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["status", "restored", "pending", "source", "version"])
async def test_current_state_is_rechecked_under_locks(fixture, change):
    document, row = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    async with fixture.sessions() as db, db.begin():
        if change == "status":
            await db.execute(update(Document).where(Document.id == document.id).values(status="failed"))
        elif change == "source":
            await db.execute(update(Document).where(Document.id == document.id).values(file_size=document.file_size + 1))
        elif change == "version":
            await db.execute(update(Document).where(Document.id == document.id).values(current_version_id=None))
        elif change == "pending":
            await db.execute(update(IngestionJob).where(IngestionJob.document_id == document.id).values(status="RUNNING"))
        else:
            db.add(DocumentChunk(point_id=row.point_id, document_id=row.document_id, chunk_index=row.chunk_index,
                content=row.content, embedding=row.embedding, metadata_json=row.metadata_json))
    expected = {"status":"state_changed", "source":"snapshot_changed", "version":"snapshot_changed", "pending":"pending_job", "restored":"working_index"}[change]
    assert (await apply_recovery_plan(fixture.sessions, plan, digest(plan)))[0]["result"] == expected
    assert (await job_state(fixture, document.id))[1] == 1


@pytest.mark.asyncio
async def test_expired_or_unreviewed_receipts_are_rejected_without_mutation(fixture):
    document, _ = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    with pytest.raises(ValueError, match="digest"):
        await apply_recovery_plan(fixture.sessions, plan, "0"*64)
    stale = copy.deepcopy(plan)
    stale["created_at"] = int(time.time())-901
    with pytest.raises(ValueError, match="expired"):
        await apply_recovery_plan(fixture.sessions, stale, digest(stale))
    repeated = copy.deepcopy(plan)
    repeated["entries"] *= 2
    with pytest.raises(ValueError, match="repeated"):
        await apply_recovery_plan(fixture.sessions, repeated, digest(repeated))
    assert await job_state(fixture, document.id) == ("SUCCEEDED", 1, 1)


@pytest.mark.asyncio
async def test_deleted_source_is_not_resurrected_by_a_prior_receipt(fixture):
    document, _ = await missing(fixture)
    plan = await build_recovery_plan(fixture.sessions, scope_for(fixture))
    async with fixture.sessions() as db, db.begin():
        await delete_registered_document(db, "standalone", document.id, "search_sphere", fixture.owner_id)
    assert (await apply_recovery_plan(fixture.sessions, plan, digest(plan)))[0]["result"] == "source_unavailable"
    assert (await job_state(fixture, document.id))[0] == "FAILED"


@pytest.mark.asyncio
async def test_paginated_bounded_dry_run_and_old_model_vectors(fixture):
    await approve(fixture, subject=fixture.owner_id)
    ids = []
    for _ in range(3):
        doc = await upload_native(fixture)
        ids.append(doc.id)
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(Document).where(Document.id.in_(ids)).values(status="indexed", chunk_count=1))
        await db.execute(update(IngestionJob).where(IngestionJob.document_id.in_(ids)).values(status="SUCCEEDED"))
        db.add(DocumentChunk(point_id="old-model-"+fixture.owner_id, document_id=ids[0], chunk_index=0,
            content="PUBLIC OLD MODEL FIXTURE", embedding=[1.0]+[0.0]*383,
            metadata_json={"source_record_id":ids[0], "embedding_model":"all-MiniLM-L6-v2"}))
    first = await build_recovery_plan(fixture.sessions, scope_for(fixture), limit=2)
    second = await build_recovery_plan(fixture.sessions, scope_for(fixture), limit=2, after_id=first["next_after_id"])
    assert len(first["entries"]) == 2 and len(second["entries"]) == 1
    assert {row["id"] for row in first["entries"]+second["entries"]} == set(ids)
    assert all(row["action"] == "enqueue" for row in first["entries"]+second["entries"])
    for limit in (0, 21, True):
        with pytest.raises(ValueError, match="batch"):
            await build_recovery_plan(fixture.sessions, scope_for(fixture), limit=limit)


@pytest.mark.asyncio
async def test_external_recovery_is_exactly_client_tenant_collection_and_subject_scoped(fixture):
    client_id = fixture.owner_id
    dimensions = [("tenant-a","class-a","subject-a"), ("tenant-a","class-b","subject-a"),
                  ("tenant-a","class-a","subject-b"), ("tenant-b","class-a","subject-a")]
    async with fixture.sessions() as db, db.begin():
        db.add(ServiceClient(client_id=client_id, name="Public synthetic recovery client",
            api_key_hash=hashlib.sha256(b"synthetic-recovery-client-key").hexdigest(), api_key_prefix="synthetic",
            allowed_scopes=["documents:write"], authorized_tenants=["tenant-a","tenant-b"]))
    documents = []
    for index, (tenant, collection, subject) in enumerate(dimensions):
        await approve(fixture, client_id, tenant, subject, collection)
        key = f"synthetic-recovery/{fixture.owner_id}/{index}.pdf"
        content = pdf()
        await fixture.storage.upload(key, content, "application/pdf")
        async with fixture.sessions() as db, db.begin():
            row, job = await register_external_document(db, {"source_system":client_id, "tenant_id":tenant,
                "collection_id":collection, "owner_subject_id":subject, "external_patient_id":subject,
                "external_document_id":"collision" if index in {0,3} else f"fixture-{index}", "storage_path":key,
                "file_name":"public.pdf", "mime_type":"application/pdf", "file_size":len(content), "document_type":"PUBLIC"})
            fixture.external_ids.append(row.id)
            documents.append(row.id)
            row.status, row.chunk_count, job.status = "READY", 1, "SUCCEEDED"
    scope = RecoveryScope("external", client_id, *dimensions[0])
    plan = await build_recovery_plan(fixture.sessions, scope)
    assert [row["id"] for row in plan["entries"]] == [documents[0]]
    forged = copy.deepcopy(plan)
    forged["entries"][0]["id"] = documents[1]
    assert (await apply_recovery_plan(fixture.sessions, forged, digest(forged)))[0]["result"] == "source_unavailable"
    assert (await apply_recovery_plan(fixture.sessions, plan, digest(plan)))[0]["result"] == "enqueued"
    assert await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage, document_kinds=("external",)).run_once()
    assert not (await build_recovery_plan(fixture.sessions, scope))["entries"]
    for identity in documents[1:]:
        assert (await job_state(fixture, identity))[1] == 1
    async with fixture.sessions() as db:
        assert (await db.get(ExternalDocument, documents[0])).status == "READY"
