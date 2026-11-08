"""Real PostgreSQL/pgvector deletion fences, recovery, and physical receipts."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from ingestion_test_support import upload_native
from sqlalchemy import func, select, update
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.external_document import ExternalDocument
from src.repositories.document_deletion import cleanup_receipt, find_deletion
from src.repositories.external_registration import register_external_document
from src.repositories.ingestion_queue import IngestionQueue
from src.routers.internal_medical_documents import delete_medical_document
from src.routers.internal_medical_ingestion import delete_medical_document_index
from src.security.medical_context import patient_collection_id
from src.security.service_context import ServiceContext
from src.services.cleanup_worker import DocumentCleanupWorker
from src.services.document_deletion import delete_registered_document
from src.services.document_service import DocumentService
from src.services.generic_document_service import GenericDocumentService
from src.services.ingestion_publication import publish_ingestion
from src.services.ingestion_source import load_ingestion_source


def chunk(document_id, **scope):
    return DocumentChunk(
        document_id=document_id, point_id=str(uuid.uuid4()), chunk_index=0,
        content="Public synthetic cleanup marker", embedding=[1.0] + [0.0] * 383, **scope,
    )


async def native_with_vector(fixture):
    doc = await upload_native(fixture)
    async with fixture.sessions() as db, db.begin():
        db.add(chunk(doc.id))
    return doc


@pytest.mark.asyncio
async def test_delete_fences_running_ingestion_and_revokes_before_object_retry(fixture):
    doc = await native_with_vector(fixture)
    queue = IngestionQueue(fixture.sessions)
    lease = await queue.claim()
    async with fixture.sessions() as db:
        source = await load_ingestion_source(db, "standalone", doc.id)
        deletion = await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.commit()
        assert await db.get(Document, doc.id) is None
        assert (await cleanup_receipt(db, deletion))["vectors_remaining"] == 0
    assert await fixture.storage.exists(doc.storage_key)  # Still private; retry metadata retained.
    assert not await publish_ingestion(fixture.sessions, queue, lease, source, SimpleNamespace())
    broken = SimpleNamespace(delete=AsyncMock(side_effect=TimeoutError), exists=AsyncMock())
    await DocumentCleanupWorker(fixture.sessions, lambda: broken).run_once(deletion.id)
    async with fixture.sessions() as db:
        pending = await db.get(DocumentDeletion, deletion.id)
        assert (await cleanup_receipt(db, pending))["status"] == "pending"
        assert pending.error_code == "object_cleanup_unverified"
        await db.execute(update(DocumentDeletion).where(DocumentDeletion.id == deletion.id).values(available_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
    assert await DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage).run_once(deletion.id)
    assert not await fixture.storage.exists(doc.storage_key)
    async with fixture.sessions() as db:
        row = await find_deletion(db, "standalone", "search_sphere", fixture.owner_id, doc.id)
        receipt = await cleanup_receipt(db, row)
        assert receipt["status"] == "complete" and receipt["attempts"] == 2
        replay = await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        assert replay.id == deletion.id


@pytest.mark.asyncio
async def test_failed_relational_delete_rolls_back_vector_purge_and_fence(fixture, monkeypatch):
    doc = await native_with_vector(fixture)
    async with fixture.sessions() as db:
        monkeypatch.setattr(db, "delete", AsyncMock(side_effect=RuntimeError("synthetic DB failure")))
        with pytest.raises(RuntimeError):
            await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.rollback()
    async with fixture.sessions() as db:
        assert await db.get(Document, doc.id)
        assert (await db.execute(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == doc.id))).scalar_one() == 1
        assert await find_deletion(db, "standalone", "search_sphere", fixture.owner_id, doc.id) is None


@pytest.mark.asyncio
async def test_same_document_id_in_other_client_tenant_and_native_is_preserved(fixture):
    document_id = str(uuid.uuid4())
    async with fixture.sessions() as db, db.begin():
        for client_id, tenant in [(fixture.owner_id, "school-a"), (fixture.owner_id, "school-b"), ("another-client", fixture.owner_id)]:
            doc = ExternalDocument(
                source_system=client_id, tenant_id=tenant, external_document_id=document_id,
                collection_id="class-a", owner_subject_id=fixture.owner_id,
                storage_path=f"synthetic/{uuid.uuid4()}.pdf", file_name="public.pdf", mime_type="application/pdf",
                file_size=100, document_type="PUBLIC", status="READY",
            )
            db.add(doc)
            await db.flush()
            fixture.external_ids.append(doc.id)
            db.add(chunk(document_id, client_id=client_id, tenant_id=tenant, collection_id="class-a", owner_subject_id=fixture.owner_id,
                         metadata_json={"source_record_id": doc.id}))
        db.add(chunk(document_id))
    context = ServiceContext(fixture.owner_id, "school-a", subject_id=fixture.owner_id, collection_id="class-a")
    async with fixture.sessions() as db:
        deletion = await delete_registered_document(db, "external", document_id, fixture.owner_id, "school-a", context=context)
        await db.commit()
    async with fixture.sessions() as db:
        assert (await cleanup_receipt(db, deletion))["vectors_remaining"] == 0
        assert (await db.execute(select(func.count()).select_from(DocumentChunk).where(DocumentChunk.document_id == document_id))).scalar_one() == 3
        wrong = ServiceContext(fixture.owner_id, "school-a", subject_id="another-subject")
        with pytest.raises(HTTPException) as denied:
            await find_deletion(db, "external", fixture.owner_id, "school-a", document_id, context=wrong)
        assert denied.value.status_code == 403
        assert await find_deletion(db, "external", fixture.owner_id, "school-b", document_id) is None
        with pytest.raises(HTTPException) as replay:
            await register_external_document(db, {"source_system": fixture.owner_id, "tenant_id": "school-a", "external_document_id": document_id})
        assert replay.value.status_code == 409
        await db.rollback()
        await db.execute(DocumentChunk.__table__.delete().where(DocumentChunk.document_id == document_id))
        await db.commit()


@pytest.mark.asyncio
async def test_cleanup_claims_once_and_recovers_expired_process(fixture):
    doc = await native_with_vector(fixture)
    async with fixture.sessions() as db:
        deletion = await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.commit()
    calls = 0
    async def delete_once(key):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return await fixture.storage.delete(key)
    storage = SimpleNamespace(delete=delete_once, exists=fixture.storage.exists)
    results = await asyncio.gather(*[DocumentCleanupWorker(fixture.sessions, lambda: storage).run_once(deletion.id) for _ in range(8)])
    assert results.count(True) == 1 and calls == 1
    async with fixture.sessions() as db:
        # Simulate losing a process after claiming but before writing its receipt.
        await db.execute(update(DocumentDeletion).where(DocumentDeletion.id == deletion.id).values(
            status="PROCESSING", lease_token=str(uuid.uuid4()), lease_expires_at=datetime.now(UTC) - timedelta(seconds=1), object_deleted=False,
        ))
        await db.commit()
    assert await DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage).run_once(deletion.id)
    async with fixture.sessions() as db:
        assert (await cleanup_receipt(db, await db.get(DocumentDeletion, deletion.id)))["status"] == "complete"


@pytest.mark.asyncio
async def test_storage_outage_cannot_be_recorded_as_object_absence(fixture):
    doc = await native_with_vector(fixture)
    async with fixture.sessions() as db:
        deletion = await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.commit()
    storage = SimpleNamespace(delete=AsyncMock(return_value=True), exists=AsyncMock(side_effect=RuntimeError("synthetic 503")))
    await DocumentCleanupWorker(fixture.sessions, lambda: storage).run_once(deletion.id)
    async with fixture.sessions() as db:
        receipt = await cleanup_receipt(db, await db.get(DocumentDeletion, deletion.id))
        assert receipt["status"] == "pending" and receipt["object_deleted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["standalone", "generic", "medical-object", "medical-index"])
async def test_delete_acknowledges_durable_revocation_without_waiting_for_storage(fixture, monkeypatch, surface):
    """A slow private object store cannot delay DELETE after its SQL commit."""
    async def blocked_delete(key):
        await asyncio.Event().wait()

    unavailable = SimpleNamespace(delete=AsyncMock(side_effect=blocked_delete), exists=fixture.storage.exists)
    monkeypatch.setattr("src.storage.object_storage.get_object_storage", lambda: unavailable)
    if surface == "standalone":
        document = await native_with_vector(fixture)
        document_id, client_id, tenant_id = document.id, "search_sphere", fixture.owner_id
        storage_key, kind = document.storage_key, "standalone"
    else:
        document_id, kind = str(uuid.uuid4()), "external"
        client_id = fixture.owner_id if surface == "generic" else "quick_clinic"
        tenant_id = fixture.owner_id
        collection = patient_collection_id(fixture.owner_id)
        storage_key = f"medical-documents/{fixture.owner_id}/{document_id}/public.pdf"
        await fixture.storage.upload(storage_key, b"%PDF-public-synthetic-cleanup")
        context = ServiceContext(client_id, tenant_id, subject_id=fixture.owner_id, collection_id=collection)
        async with fixture.sessions() as db, db.begin():
            document = ExternalDocument(
                source_system=client_id, tenant_id=tenant_id, external_document_id=document_id,
                external_patient_id=fixture.owner_id, owner_subject_id=fixture.owner_id,
                collection_id=collection, storage_path=storage_key, file_name="public.pdf",
                mime_type="application/pdf", file_size=29, status="READY", document_type="PUBLIC",
            )
            db.add(document)
            await db.flush()
            fixture.external_ids.append(document.id)
            db.add(chunk(document_id, client_id=client_id, tenant_id=tenant_id,
                collection_id=collection, owner_subject_id=fixture.owner_id,
                metadata_json={"source_record_id": document.id}))

    async def request_delete():
        async with fixture.sessions() as db:
            if surface == "standalone":
                await DocumentService(db, unavailable).delete_user_document(document_id, fixture.owner_id)
            elif surface == "generic":
                assert await GenericDocumentService(db, unavailable).delete_document(context, document_id)
            elif surface == "medical-object":
                result = await delete_medical_document(storage_path=storage_key, body=None, _auth=context, storage=unavailable, db=db)
                assert result.success
            else:
                result = await delete_medical_document_index(document_id, _auth=context, session=db)
                assert result.success

    await asyncio.wait_for(request_delete(), timeout=2)
    unavailable.delete.assert_not_awaited()
    async with fixture.sessions() as db:
        deletion = await find_deletion(db, kind, client_id, tenant_id, document_id)
        receipt = await cleanup_receipt(db, deletion)
        assert receipt["status"] == "pending" and receipt["vectors_remaining"] == 0
        assert receipt["attempts"] == 0 and receipt["object_deleted"] is False
        assert await db.get(Document if kind == "standalone" else ExternalDocument, document.id) is None
    assert await fixture.storage.exists(storage_key)
    # A fresh worker recovers the persisted intent without the original request.
    assert await DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage).run_once(deletion.id)
    async with fixture.sessions() as db:
        row = await find_deletion(db, kind, client_id, tenant_id, document_id)
        assert (await cleanup_receipt(db, row))["status"] == "complete"
    await asyncio.wait_for(request_delete(), timeout=2)
    unavailable.delete.assert_not_awaited()
    assert fixture.calls == []
