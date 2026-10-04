from datetime import datetime, timedelta, timezone
import uuid
from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.config import settings
from src.db import AsyncSessionLocal
from src.main import app
from src.models.external_document import ExternalDocument

TEST_SERVICE_SECRET = "test-service-secret"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer invalid-service-secret"}


@pytest.fixture(autouse=True)
def ensure_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured during tests."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


@pytest.mark.asyncio
async def test_ingest_missing_auth_header():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-documents/doc-123/ingest",
            json={
                "patientId": "pat-123",
                "storagePath": "medical-documents/pat-123/doc-123/report.pdf",
                "fileName": "report.pdf",
                "mimeType": "application/pdf",
                "fileSize": 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp.status_code == 401
        assert "Authorization header is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_invalid_auth_secret():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-documents/doc-123/ingest",
            headers=INVALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": "medical-documents/pat-123/doc-123/report.pdf",
                "fileName": "report.pdf",
                "mimeType": "application/pdf",
                "fileSize": 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp.status_code == 401
        assert "Invalid service secret" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_validation_errors():
    doc_id = f"doc-val-{uuid.uuid4().hex[:6]}"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Invalid MIME type
        resp_mime = await client.post(
            f"/internal/medical-documents/{doc_id}/ingest",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": f"medical-documents/pat-123/{doc_id}/archive.zip",
                "fileName": "archive.zip",
                "mimeType": "application/zip",
                "fileSize": 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp_mime.status_code == 400
        assert "Unsupported mime type" in resp_mime.json()["detail"]

        # 2. Invalid storage path prefix
        resp_path = await client.post(
            f"/internal/medical-documents/{doc_id}/ingest",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": f"other-bucket/pat-123/{doc_id}/report.pdf",
                "fileName": "report.pdf",
                "mimeType": "application/pdf",
                "fileSize": 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp_path.status_code == 400
        assert "Must begin with 'medical-documents/'" in resp_path.json()["detail"]

        # 3. Path traversal in storage path
        resp_trav = await client.post(
            f"/internal/medical-documents/{doc_id}/ingest",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": f"medical-documents/pat-123/../../etc/passwd",
                "fileName": "passwd",
                "mimeType": "application/pdf",
                "fileSize": 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp_trav.status_code == 400
        assert "Path traversal" in resp_trav.json()["detail"]

        # 4. Negative / zero file size
        resp_size = await client.post(
            f"/internal/medical-documents/{doc_id}/ingest",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": f"medical-documents/pat-123/{doc_id}/report.pdf",
                "fileName": "report.pdf",
                "mimeType": "application/pdf",
                "fileSize": 0,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp_size.status_code == 400
        assert "file_size must be a positive integer" in resp_size.json()["detail"]

        # 5. File size exceeds 50MB
        resp_too_large = await client.post(
            f"/internal/medical-documents/{doc_id}/ingest",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "storagePath": f"medical-documents/pat-123/{doc_id}/large.pdf",
                "fileName": "large.pdf",
                "mimeType": "application/pdf",
                "fileSize": 55 * 1024 * 1024,
                "documentType": "LAB_REPORT",
            },
        )
        assert resp_too_large.status_code == 413
        assert "File exceeds maximum allowed size" in resp_too_large.json()["detail"]


@pytest.mark.asyncio
async def test_ingest_success_and_idempotency():
    doc_id = f"doc-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-{uuid.uuid4().hex[:8]}"
    storage_path = f"medical-documents/{patient_id}/{doc_id}/blood_test.pdf"

    transport = ASGITransport(app=app)
    with patch("src.routers.internal_medical_ingestion.enqueue_medical_document", return_value=True) as mock_enqueue:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. First ingest call -> creates QUEUED record and enqueues task
            resp1 = await client.post(
                f"/internal/medical-documents/{doc_id}/ingest",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_id,
                    "storagePath": storage_path,
                    "fileName": "blood_test.pdf",
                    "mimeType": "application/pdf",
                    "fileSize": 123456,
                    "documentType": "LAB_REPORT",
                    "reportDate": "2026-10-01T00:00:00.000Z",
                },
            )
            assert resp1.status_code == 202
            data1 = resp1.json()
            assert data1["documentId"] == doc_id
            assert data1["status"] == "QUEUED"
            mock_enqueue.assert_called_with(doc_id, request_id=ANY)

            # Check DB record
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ExternalDocument).where(
                        ExternalDocument.source_system == "quick_clinic",
                        ExternalDocument.external_document_id == doc_id,
                    )
                )
                record = result.scalar_one_or_none()
                assert record is not None
                assert record.external_patient_id == patient_id
                assert record.status == "QUEUED"
                assert record.document_type == "LAB_REPORT"

            # 2. Second ingest call (idempotent re-ingest)
            resp2 = await client.post(
                f"/internal/medical-documents/{doc_id}/ingest",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_id,
                    "storagePath": storage_path,
                    "fileName": "blood_test_v2.pdf",
                    "mimeType": "application/pdf",
                    "fileSize": 234567,
                    "documentType": "DISCHARGE_SUMMARY",
                    "reportDate": "2026-10-02T00:00:00.000Z",
                },
            )
            assert resp2.status_code == 202
            data2 = resp2.json()
            assert data2["documentId"] == doc_id
            assert data2["status"] == "QUEUED"

            # Verify no duplicates created, record was updated
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ExternalDocument).where(
                        ExternalDocument.source_system == "quick_clinic",
                        ExternalDocument.external_document_id == doc_id,
                    )
                )
                records = result.scalars().all()
                assert len(records) == 1
                updated = records[0]
                assert updated.file_name == "blood_test_v2.pdf"
                assert updated.document_type == "DISCHARGE_SUMMARY"
                assert updated.file_size == 234567


@pytest.mark.asyncio
async def test_get_status_lifecycle():
    doc_id = f"doc-status-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-{uuid.uuid4().hex[:8]}"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Non-existent document -> 404
        resp_404 = await client.get(
            f"/internal/medical-documents/{doc_id}/status",
            headers=VALID_AUTH_HEADER,
        )
        assert resp_404.status_code == 404
        assert "not found" in resp_404.json()["detail"]

        # Insert record directly in DB
        async with AsyncSessionLocal() as session:
            record = ExternalDocument(
                source_system="quick_clinic",
                external_document_id=doc_id,
                external_patient_id=patient_id,
                storage_path=f"medical-documents/{patient_id}/{doc_id}/report.png",
                file_name="report.png",
                mime_type="image/png",
                file_size=5000,
                document_type="PRESCRIPTION",
                status="READY",
            )
            session.add(record)
            await session.commit()

        # 2. Existing document -> 200 with status
        resp_200 = await client.get(
            f"/internal/medical-documents/{doc_id}/status",
            headers=VALID_AUTH_HEADER,
        )
        assert resp_200.status_code == 200
        data = resp_200.json()
        assert data["documentId"] == doc_id
        assert data["status"] == "READY"
        assert data["error"] is None


@pytest.mark.asyncio
async def test_delete_medical_document_index():
    doc_id = f"doc-del-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-{uuid.uuid4().hex[:8]}"

    # Seed record
    async with AsyncSessionLocal() as session:
        record = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=doc_id,
            external_patient_id=patient_id,
            storage_path=f"medical-documents/{patient_id}/{doc_id}/report.pdf",
            file_name="report.pdf",
            mime_type="application/pdf",
            file_size=5000,
            document_type="LAB_REPORT",
            status="READY",
        )
        session.add(record)
        await session.commit()

    transport = ASGITransport(app=app)
    with patch("src.routers.internal_medical_ingestion.QdrantVectorStore") as mock_qdrant_cls:
        mock_instance = AsyncMock()
        mock_instance.delete_document_points = AsyncMock(return_value=True)
        mock_instance.close = AsyncMock()
        mock_qdrant_cls.return_value = mock_instance

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(
                f"/internal/medical-documents/{doc_id}/index",
                headers=VALID_AUTH_HEADER,
            )
            assert resp.status_code == 200
            assert resp.json()["success"] is True

            # Verify Qdrant delete was called
            mock_instance.delete_document_points.assert_awaited_once_with(doc_id)

    # Verify record was deleted from database
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ExternalDocument).where(
                ExternalDocument.source_system == "quick_clinic",
                ExternalDocument.external_document_id == doc_id,
            )
        )
        assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_stale_documents_recovery():
    doc_id1 = f"doc-stale-1-{uuid.uuid4().hex[:8]}"
    doc_id2 = f"doc-stale-2-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-{uuid.uuid4().hex[:8]}"
    old_time = datetime.now(timezone.utc) - timedelta(minutes=30)

    async with AsyncSessionLocal() as session:
        doc1 = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=doc_id1,
            external_patient_id=patient_id,
            storage_path="path1",
            file_name="f1.pdf",
            mime_type="application/pdf",
            file_size=1000,
            document_type="LAB_REPORT",
            status="PROCESSING",
            updated_at=old_time,
        )
        doc2 = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=doc_id2,
            external_patient_id=patient_id,
            storage_path="path2",
            file_name="f2.pdf",
            mime_type="application/pdf",
            file_size=1000,
            document_type="LAB_REPORT",
            status="PROCESSING",
            updated_at=old_time,
        )
        session.add_all([doc1, doc2])
        await session.commit()

    transport = ASGITransport(app=app)
    with patch("src.routers.internal_medical_ingestion.enqueue_medical_document", return_value=True) as mock_enqueue:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Recover with action=fail
            resp1 = await client.post(
                "/internal/medical-documents/stale/recover?thresholdMinutes=15&action=fail",
                headers=VALID_AUTH_HEADER,
            )
            assert resp1.status_code == 200
            data1 = resp1.json()
            assert data1["staleCount"] >= 2
            assert doc_id1 in data1["recoveredDocumentIds"]
            assert doc_id2 in data1["recoveredDocumentIds"]
            assert data1["action"] == "fail"

            # Verify status in database changed to FAILED
            async with AsyncSessionLocal() as session:
                res1 = await session.execute(
                    select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id1)
                )
                d1 = res1.scalar_one()
                assert d1.status == "FAILED"
                assert "timed out" in d1.processing_error

            # 2. Reset doc2 to PROCESSING with old time and recover with action=requeue
            async with AsyncSessionLocal() as session:
                res2 = await session.execute(
                    select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id2)
                )
                d2 = res2.scalar_one()
                d2.status = "PROCESSING"
                d2.updated_at = old_time
                await session.commit()

            resp2 = await client.post(
                "/internal/medical-documents/stale/recover?thresholdMinutes=15&action=requeue",
                headers=VALID_AUTH_HEADER,
            )
            assert resp2.status_code == 200
            data2 = resp2.json()
            assert doc_id2 in data2["recoveredDocumentIds"]
            assert data2["action"] == "requeue"
            mock_enqueue.assert_called_with(doc_id2, request_id=ANY)

            async with AsyncSessionLocal() as session:
                res2 = await session.execute(
                    select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id2)
                )
                d2_final = res2.scalar_one()
                assert d2_final.status == "QUEUED"

