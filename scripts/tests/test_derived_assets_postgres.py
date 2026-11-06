"""Private source crops have durable cleanup intents before object upload."""

import json
from datetime import UTC, datetime, timedelta
from io import BytesIO

import pymupdf
import pytest
from fastapi import HTTPException, UploadFile
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from PIL import Image
from sqlalchemy import func, select, update
from src.inference.cohere_gateway import CohereGateway
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence
from src.models.ingestion_job import IngestionJob
from src.models.user import User
from src.repositories.document_deletion import cleanup_receipt, find_deletion
from src.services.cleanup_worker import DocumentCleanupWorker
from src.services.document_deletion import delete_registered_document
from src.services.document_service import DocumentService
from src.services.ingestion_source import IngestionSourceError
from src.services.ingestion_worker import PostgresIngestionWorker
from src.services.structured_evidence import structured_crop_access


async def upload_public_scan(fixture, monkeypatch):
    original = CohereGateway._request
    async def provider(self, endpoint, payload):
        if endpoint == "/v2/chat":
            region = {"kind": "table", "bbox": [0, 0, 1000, 1000], "headers": ["Public fixture", "Value"],
                      "rows": [["PUBLIC-SCAN-441", "42"]]}
            return {"finish_reason": "COMPLETE", "message": {"content": [{"type": "text", "text": json.dumps({"regions": [region]})}]}}
        return await original(self, endpoint, payload)
    monkeypatch.setattr(CohereGateway, "_request", provider)
    buffer = BytesIO()
    Image.new("RGB", (400, 300), "white").save(buffer, format="PNG")
    pdf = pymupdf.open()
    pdf.new_page().insert_image(pymupdf.Rect(40, 80, 440, 380), stream=buffer.getvalue())
    content = pdf.tobytes()
    pdf.close()
    await approve(fixture, subject=fixture.owner_id)
    async with fixture.sessions() as db:
        return await DocumentService(db, fixture.storage).upload_document(
            UploadFile(filename="public-scan.pdf", file=BytesIO(content)), await db.get(User, fixture.owner_id),
        )


@pytest.mark.asyncio
async def test_published_crop_is_private_linked_and_deleted_before_complete_receipt(fixture, monkeypatch):
    doc = await upload_public_scan(fixture, monkeypatch)
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        evidence = (await db.execute(select(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one()
        asset = await db.get(DocumentDeletion, evidence.data_json["asset_id"])
        assert asset.status == "PUBLISHED" and await fixture.storage.exists(asset.storage_key)
        chunks = (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc.id))).scalars().all()
        assert chunks[0].metadata_json["provenance"][0]["asset_id"] == asset.id
        access = await structured_crop_access(db, fixture.storage, "standalone", doc.id, evidence.evidence_id)
        assert access["expires_in"] == 300 and "token=" in access["url"]
        with pytest.raises(HTTPException) as denied:
            await structured_crop_access(db, fixture.storage, "standalone", doc.id, evidence.evidence_id, "another-asset")
        assert denied.value.status_code == 404
        deletion = await delete_registered_document(db, "standalone", doc.id, "search_sphere", fixture.owner_id)
        await db.commit()
    cleanup = DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage)
    assert await cleanup.run_once(deletion.id)
    async with fixture.sessions() as db:
        row = await find_deletion(db, "standalone", "search_sphere", fixture.owner_id, doc.id)
        receipt = await cleanup_receipt(db, row)
        assert receipt["status"] == "pending" and receipt["derived_objects_remaining"] == 1
        await db.execute(update(DocumentDeletion).where(DocumentDeletion.id == asset.id).values(available_at=func.now()))
        await db.commit()
    assert await cleanup.run_once(asset.id)
    assert not await fixture.storage.exists(asset.storage_key)
    async with fixture.sessions() as db:
        assert (await cleanup_receipt(db, await db.get(DocumentDeletion, deletion.id)))["status"] == "complete"


@pytest.mark.asyncio
async def test_interruption_before_publication_keeps_a_recoverable_crop_cleanup_intent(fixture, monkeypatch):
    doc = await upload_public_scan(fixture, monkeypatch)
    async def abort(*args):
        raise IngestionSourceError("processing_failed", retryable=True)
    monkeypatch.setattr("src.services.ingestion_worker.publish_ingestion", abort)
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        asset = (await db.execute(select(DocumentDeletion).where(DocumentDeletion.kind == "asset", DocumentDeletion.source_record_id == doc.id))).scalar_one()
        assert asset.status == "STAGED" and await fixture.storage.exists(asset.storage_key)
        assert (await db.execute(select(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one_or_none() is None
        await db.execute(update(DocumentDeletion).where(DocumentDeletion.id == asset.id).values(available_at=func.now()))
        await db.execute(update(IngestionJob).where(IngestionJob.document_id == doc.id).values(
            status="RUNNING", lease_token=asset.lease_token, lease_expires_at=datetime.now(UTC) + timedelta(seconds=120),
        ))
        await db.commit()
    cleanup = DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage)
    assert not await cleanup.run_once(asset.id)  # A live ingestion lease protects its staged crop.
    async with fixture.sessions() as db:
        await db.execute(update(IngestionJob).where(IngestionJob.document_id == doc.id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
    assert await cleanup.run_once(asset.id)
    assert not await fixture.storage.exists(asset.storage_key)


@pytest.mark.asyncio
async def test_successful_reindex_switches_crop_reference_and_cleans_old_private_object(fixture, monkeypatch):
    doc = await upload_public_scan(fixture, monkeypatch)
    worker = PostgresIngestionWorker(fixture.sessions, storage_factory=lambda: fixture.storage)
    await worker.run_once()
    async with fixture.sessions() as db:
        old = (await db.execute(select(DocumentDeletion).where(DocumentDeletion.kind == "asset", DocumentDeletion.source_record_id == doc.id))).scalar_one()
        await DocumentService(db, fixture.storage).reindex_user_document(doc.id, fixture.owner_id)
    assert await worker.run_once()
    async with fixture.sessions() as db:
        evidence = (await db.execute(select(DocumentEvidence).where(DocumentEvidence.source_record_id == doc.id))).scalar_one()
        new = await db.get(DocumentDeletion, evidence.data_json["asset_id"])
        assert new.id != old.id and new.status == "PUBLISHED"
        assert (await db.get(DocumentDeletion, old.id)).status == "QUEUED"
    assert await DocumentCleanupWorker(fixture.sessions, lambda: fixture.storage).run_once(old.id)
    assert not await fixture.storage.exists(old.storage_key) and await fixture.storage.exists(new.storage_key)
