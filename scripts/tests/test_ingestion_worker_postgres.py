"""Real PostgreSQL processing and publication; provider HTTP is synthetic and isolated."""

import asyncio
import hashlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import approve, pdf, upload_native
from ingestion_test_support import fixture as fixture  # noqa: PLC0414 -- pytest fixture registration
from sqlalchemy import delete, select, update
from src.config import settings
from src.db import get_db
from src.inference.policy import ProviderError
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.ingestion_job import IngestionJob
from src.models.medical_observation import MedicalObservation
from src.models.service_client import ServiceClient
from src.repositories.external_registration import register_external_document
from src.repositories.ingestion_queue import IngestionQueue
from src.services.document_service import DocumentService
from src.services.ingestion_recovery import adopt_legacy_pending
from src.services.ingestion_worker import PostgresIngestionWorker


@pytest.mark.asyncio
async def test_native_upload_outbox_processing_and_status_are_durable(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    assert document.status == "queued" and document.job.status == "QUEUED"
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        current = await DocumentService(db, fixture.storage).get_user_document(document.id, fixture.owner_id)
        assert current.status == "indexed" and current.chunk_count > 0
        assert current.job.status == "SUCCEEDED" and current.job.attempts == 1
        rows = (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == document.id))).scalars().all()
        assert rows and all(row.metadata_json["embedding_model"] == "embed-english-light-v3.0" for row in rows)
        provenance = rows[0].metadata_json["provenance"]
        assert provenance and provenance[0]["page_number"] == 1
        assert len(provenance[0]["bbox"]) == 4 and len(provenance[0]["document_sha256"]) == 64
    assert fixture.calls and not await worker.run_once()


@pytest.mark.asyncio
async def test_unapproved_content_fails_before_download_or_provider(fixture):
    document = await upload_native(fixture)
    storage = SimpleNamespace(download=AsyncMock())
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: storage)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        row = await db.get(Document, document.id)
        job = (await db.execute(select(IngestionJob).where(IngestionJob.document_id == document.id))).scalar_one()
        assert row.status == "failed:provider_not_approved" and job.status == "FAILED"
        assert job.error_code == "provider_not_approved"
    storage.download.assert_not_awaited()
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_external_registration_duplicates_and_scope_collision_are_atomic(fixture):
    from fastapi import HTTPException

    values = {
        "source_system": fixture.owner_id, "tenant_id": "synthetic-school",
        "external_document_id": "public-fixture", "external_patient_id": "synthetic-subject",
        "owner_subject_id": "synthetic-subject", "collection_id": "synthetic-class",
        "storage_path": "synthetic/path", "file_name": "public.txt", "mime_type": "text/plain",
        "file_size": 100, "document_type": "PUBLIC_FIXTURE",
    }
    async with fixture.sessions() as db, db.begin():
        first, first_job = await register_external_document(db, values)
    async def duplicate_delivery():
        async with fixture.sessions() as db, db.begin():
            row, job = await register_external_document(db, values)
            return row.id, job.id

    assert set(await asyncio.gather(*(duplicate_delivery() for _ in range(8)))) == {(first.id, first_job.id)}
    async with fixture.sessions() as db, db.begin():
        duplicate, duplicate_job = await register_external_document(db, values)
        assert duplicate.id == first.id and duplicate_job.id == first_job.id
        assert duplicate_job.generation == 1
    async with fixture.sessions() as db:
        with pytest.raises(HTTPException) as failure:
            await register_external_document(db, {**values, "owner_subject_id": "another-subject"})
        assert failure.value.status_code == 409
    async with fixture.sessions() as db:
        transaction = await db.begin()
        await register_external_document(db, {**values, "external_document_id": "rolled-back"})
        await transaction.rollback()
    async with fixture.sessions() as db:
        assert len((await db.execute(select(IngestionJob))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_reindex_provider_failure_preserves_existing_searchable_evidence(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    await worker.run_once()
    async with fixture.sessions() as db:
        assert (await db.get(Document, document.id)).status == "indexed"
    async with fixture.sessions() as db:
        await DocumentService(db, fixture.storage).reindex_user_document(document.id, fixture.owner_id)

    async def unavailable(*args):
        raise ProviderError("Synthetic provider outage")

    failed_worker = PostgresIngestionWorker(fixture.sessions, processor=unavailable)
    for attempt in range(3):
        assert await failed_worker.run_once()
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(IngestionJob).where(IngestionJob.document_id == document.id).values(available_at=datetime.now(UTC) - timedelta(seconds=1)))
    async with fixture.sessions() as db:
        current = await DocumentService(db, fixture.storage).get_user_document(document.id, fixture.owner_id)
        assert current.status == "indexed" and current.chunk_count > 0
        assert current.job.status == "FAILED" and current.job.error_code == "provider_unavailable"
        assert (await db.execute(select(DocumentChunk.id).where(DocumentChunk.document_id == document.id))).first()
    assert not await failed_worker.run_once()


@pytest.mark.asyncio
async def test_expired_claim_is_recovered_by_a_new_worker_instance(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    queue = IngestionQueue(fixture.sessions)
    abandoned = await queue.claim()
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(IngestionJob).where(IngestionJob.id == abandoned.job_id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
    restarted = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert await restarted.run_once()
    async with fixture.sessions() as db:
        job = await db.get(IngestionJob, abandoned.job_id)
        assert job.status == "SUCCEEDED" and job.attempts == 2
        assert (await db.get(Document, document.id)).status == "indexed"
    assert not await queue.heartbeat(abandoned)


@pytest.mark.asyncio
@pytest.mark.parametrize("medical", [False, True])
async def test_external_http_upload_register_and_worker_publish(fixture, monkeypatch, medical):
    from src.main import app
    from src.security.medical_context import patient_collection_id
    from src.storage.object_storage import get_object_storage

    client_id = "quick_clinic" if medical else fixture.owner_id
    tenant = "quick_clinic_default" if medical else "synthetic-school"
    collection = patient_collection_id(fixture.owner_id) if medical else None
    service_key = "synthetic-service-key-" + fixture.owner_id
    monkeypatch.setattr(settings, "QUICK_CLINIC_SERVICE_SECRET", service_key if medical else "")
    async with fixture.sessions() as db, db.begin():
        client = ServiceClient(
            client_id=client_id, name="Synthetic integration fixture",
            api_key_hash=hashlib.sha256(service_key.encode()).hexdigest(), api_key_prefix="synthetic",
            allowed_scopes=["documents:write", "documents:read"], authorized_tenants=[tenant],
        )
        db.add(client)
        await db.flush()
        fixture.client_ids.append(client.id)
    await approve(fixture, client_id, tenant, fixture.owner_id, collection or "")

    async def database():
        async with fixture.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_object_storage] = lambda: fixture.storage
    headers = {"Authorization": f"Bearer {service_key}", "X-Client-ID": client_id, "X-Tenant-ID": tenant, "X-Subject-ID": fixture.owner_id}
    external_id = "public-fixture-" + uuid.uuid4().hex[:8]
    content = pdf("PUBLIC SYNTHETIC RECORD. BP: 120/80 mmHg. SS-MEDICAL-42.") if medical else pdf()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            upload_path = "/internal/medical-documents" if medical else "/api/v1/documents/upload"
            upload = await client.post(
                upload_path, headers=headers,
                files={"file": ("public.pdf", content, "application/pdf")},
                data={"patient_id": fixture.owner_id, "document_id": external_id} if medical else {"document_id": external_id},
            )
            assert upload.status_code == 201, upload.text
            uploaded = upload.json()
            body = {
                "patientId": fixture.owner_id, "storagePath": uploaded["storagePath"],
                "fileName": "public.pdf", "mimeType": "application/pdf", "fileSize": len(content), "documentType": "LAB_REPORT",
            } if medical else {
                "external_document_id": external_id, "owner_subject_id": fixture.owner_id,
                "storage_key": uploaded["storage_key"], "file_name": "public.pdf", "mime_type": "application/pdf",
                "file_size": len(content), "document_type": "PUBLIC_FIXTURE",
            }
            registration_path = f"/internal/medical-documents/{external_id}/ingest" if medical else "/api/v1/documents"
            registered = await client.post(registration_path, headers=headers, json=body)
            assert registered.status_code == 202, registered.text
            assert registered.json()["job"]["status"] == "QUEUED"
            async with fixture.sessions() as db:
                row = (await db.execute(select(ExternalDocument).where(ExternalDocument.source_system == client_id, ExternalDocument.external_document_id == external_id))).scalar_one()
                fixture.external_ids.append(row.id)
            worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
            assert await worker.run_once()
            status_path = f"/internal/medical-documents/{external_id}/status" if medical else f"/api/v1/documents/{external_id}"
            status = await client.get(status_path, headers=headers)
            assert status.status_code == 200 and status.json()["status"] == "READY", status.text
            assert status.json()["job"]["status"] == "SUCCEEDED"
            duplicate = await client.post(registration_path, headers=headers, json=body)
            assert duplicate.status_code == 202 and duplicate.json()["status"] == "READY"
            assert duplicate.json()["job"]["attempts"] == 1
            async with fixture.sessions() as db:
                chunks = (await db.execute(select(DocumentChunk).where(DocumentChunk.client_id == client_id, DocumentChunk.tenant_id == tenant, DocumentChunk.document_id == external_id))).scalars().all()
                assert chunks and all(chunk.owner_subject_id == fixture.owner_id for chunk in chunks)
                if medical:
                    observations = (await db.execute(select(MedicalObservation).where(MedicalObservation.external_patient_id == fixture.owner_id))).scalars().all()
                    assert any(observation.value_numeric == 120 and observation.value_secondary_numeric == 80 for observation in observations)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_policy_revocation_during_processing_prevents_publication(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)

    async def revoke(*args):
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == fixture.owner_id).values(enabled=False))

    worker = PostgresIngestionWorker(fixture.sessions, processor=revoke)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        job = (await db.execute(select(IngestionJob).where(IngestionJob.document_id == document.id))).scalar_one()
        assert job.status == "FAILED" and job.error_code == "provider_not_approved"
        assert (await db.get(Document, document.id)).status == "failed:provider_not_approved"
        assert not (await db.execute(select(DocumentChunk.id).where(DocumentChunk.document_id == document.id))).first()
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("live_legacy_lease", [False, True])
async def test_legacy_pending_adoption_preserves_live_lease_and_original_queue_age(fixture, live_legacy_lease):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    old = datetime.now(UTC) - timedelta(hours=1)
    legacy_status = f"proc:legacy-worker:{int(time.time() + 300)}:1" if live_legacy_lease else "uploaded"
    async with fixture.sessions() as db, db.begin():
        await db.execute(delete(IngestionJob).where(IngestionJob.document_id == document.id))
        await db.execute(update(Document).where(Document.id == document.id).values(status=legacy_status, created_at=old, updated_at=old))
    queue = IngestionQueue(fixture.sessions)
    adopted = await adopt_legacy_pending(fixture.sessions, queue, owner_user_id=fixture.owner_id)
    assert adopted == (0 if live_legacy_lease else 1)
    async with fixture.sessions() as db:
        current = await DocumentService(db, fixture.storage).get_user_document(document.id, fixture.owner_id)
        if live_legacy_lease:
            assert current.status == legacy_status and current.job is None
        else:
            assert current.status == "queued" and current.job.stage == "recovering"
            assert current.job.queue_age_seconds >= 3590
    if not live_legacy_lease:
        assert await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage).run_once()


@pytest.mark.asyncio
async def test_pdf_page_limit_fails_before_any_model_request(fixture):
    await approve(fixture, subject=fixture.owner_id)
    document = await upload_native(fixture)
    large = pymupdf.open()
    for _ in range(101):
        large.new_page()
    content = large.tobytes()
    large.close()
    await fixture.storage.upload(document.storage_key, content, "application/pdf")
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(Document).where(Document.id == document.id).values(file_size=len(content)))
    assert await PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage).run_once()
    async with fixture.sessions() as db:
        assert (await db.get(Document, document.id)).status == "failed:invalid_document"
    assert fixture.calls == []
