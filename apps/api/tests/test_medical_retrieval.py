from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.config import settings
from src.db import AsyncSessionLocal
from src.main import app
from src.models.external_document import ExternalDocument
from src.processing.models.search import RerankedSearchResult
from src.services.search_service import get_retriever

TEST_SERVICE_SECRET = "quick-clinic-internal-service-secret-2026"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer wrong-service-secret"}


@pytest.fixture(autouse=True)
def ensure_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured during tests."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


@pytest.mark.asyncio
async def test_medical_retrieval_auth_missing():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-retrieval/search",
            json={
                "patientId": "pat-123",
                "query": "blood pressure",
            },
        )
        assert resp.status_code == 401
        assert "Authorization header is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_medical_retrieval_auth_invalid():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-retrieval/search",
            headers=INVALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "query": "blood pressure",
            },
        )
        assert resp.status_code == 401
        assert "Invalid service secret" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_medical_retrieval_validation_empty_query():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-retrieval/search",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "query": "   ",
            },
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_medical_retrieval_validation_invalid_patient_id():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-retrieval/search",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": "../pat-hack",
                "query": "blood pressure",
            },
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
async def test_medical_retrieval_patient_isolation_pat_a_and_pat_b():
    """
    PART 19 & PART 34: Patient Isolation Test.
    Patient A uploads "Blood Pressure 120/80 on Oct 1"
    Patient B uploads "Blood Pressure 180/110 on Oct 1"
    Searching with patient_id=A returns ONLY Patient A chunk.
    Searching with patient_id=B returns ONLY Patient B chunk.
    """
    pat_a_id = f"pat-A-{uuid.uuid4().hex[:6]}"
    pat_b_id = f"pat-B-{uuid.uuid4().hex[:6]}"
    doc_a_id = f"doc-A-{uuid.uuid4().hex[:6]}"
    doc_b_id = f"doc-B-{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as session:
        doc_a = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=doc_a_id,
            external_patient_id=pat_a_id,
            storage_path=f"medical-documents/{pat_a_id}/{doc_a_id}/bp_normal.pdf",
            file_name="bp_normal.pdf",
            mime_type="application/pdf",
            file_size=2048,
            document_type="LAB_REPORT",
            report_date=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
            status="READY",
        )
        doc_b = ExternalDocument(
            source_system="quick_clinic",
            external_document_id=doc_b_id,
            external_patient_id=pat_b_id,
            storage_path=f"medical-documents/{pat_b_id}/{doc_b_id}/bp_high.pdf",
            file_name="bp_high.pdf",
            mime_type="application/pdf",
            file_size=2048,
            document_type="LAB_REPORT",
            report_date=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc),
            status="READY",
        )
        session.add(doc_a)
        session.add(doc_b)
        await session.commit()

    chunk_a = RerankedSearchResult(
        point_id="pt-a-1",
        document_id=doc_a_id,
        chunk_index=0,
        content="Patient A: Blood Pressure 120/80 mmHg recorded on Oct 1.",
        token_count=12,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=0.95,
        rrf_score=0.033,
        score=0.95,
        rank=1,
        patient_id=pat_a_id,
        source_system="quick_clinic",
        document_type="LAB_REPORT",
        report_date="2026-10-01T00:00:00+00:00",
        file_name="bp_normal.pdf",
    )

    chunk_b = RerankedSearchResult(
        point_id="pt-b-1",
        document_id=doc_b_id,
        chunk_index=0,
        content="Patient B: Blood Pressure 180/110 mmHg recorded on Oct 1.",
        token_count=12,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=0.93,
        rrf_score=0.031,
        score=0.93,
        rank=1,
        patient_id=pat_b_id,
        source_system="quick_clinic",
        document_type="LAB_REPORT",
        report_date="2026-10-01T00:00:00+00:00",
        file_name="bp_high.pdf",
    )

    mock_retriever = AsyncMock()

    async def mock_search(query, top_k, candidate_k, filters):
        assert filters["source_system"] == "quick_clinic"
        if filters["patient_id"] == pat_a_id:
            return [chunk_a]
        elif filters["patient_id"] == pat_b_id:
            return [chunk_b]
        return []

    mock_retriever.search.side_effect = mock_search

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Search as Patient A
            resp_a = await client.post(
                "/internal/medical-retrieval/search",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": pat_a_id,
                    "query": "blood pressure",
                    "limit": 5,
                },
            )
            assert resp_a.status_code == 200
            data_a = resp_a.json()
            assert len(data_a["results"]) == 1
            assert "120/80" in data_a["results"][0]["content"]
            assert "180/110" not in data_a["results"][0]["content"]
            assert data_a["results"][0]["patientId"] == pat_a_id
            assert data_a["results"][0]["documentId"] == doc_a_id
            assert data_a["results"][0]["fileName"] == "bp_normal.pdf"
            assert data_a["results"][0]["reportDate"] is not None

            # 2. Search as Patient B
            resp_b = await client.post(
                "/internal/medical-retrieval/search",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": pat_b_id,
                    "query": "blood pressure",
                    "limit": 5,
                },
            )
            assert resp_b.status_code == 200
            data_b = resp_b.json()
            assert len(data_b["results"]) == 1
            assert "180/110" in data_b["results"][0]["content"]
            assert "120/80" not in data_b["results"][0]["content"]
            assert data_b["results"][0]["patientId"] == pat_b_id
            assert data_b["results"][0]["documentId"] == doc_b_id
    finally:
        app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_medical_retrieval_only_ready_documents_and_deleted_safety():
    """
    PART 6 & PART 17: Non-READY or deleted documents are not returned.
    """
    pat_id = f"pat-{uuid.uuid4().hex[:6]}"
    ready_doc_id = f"doc-ready-{uuid.uuid4().hex[:6]}"
    failed_doc_id = f"doc-failed-{uuid.uuid4().hex[:6]}"
    deleted_doc_id = f"doc-deleted-{uuid.uuid4().hex[:6]}"

    async with AsyncSessionLocal() as session:
        session.add(
            ExternalDocument(
                source_system="quick_clinic",
                external_document_id=ready_doc_id,
                external_patient_id=pat_id,
                storage_path="path/1.pdf",
                file_name="ready.pdf",
                mime_type="application/pdf",
                file_size=1024,
                document_type="LAB_REPORT",
                status="READY",
            )
        )
        session.add(
            ExternalDocument(
                source_system="quick_clinic",
                external_document_id=failed_doc_id,
                external_patient_id=pat_id,
                storage_path="path/2.pdf",
                file_name="failed.pdf",
                mime_type="application/pdf",
                file_size=1024,
                document_type="LAB_REPORT",
                status="FAILED",
            )
        )
        # Note: deleted_doc_id is not in DB at all (was deleted)
        await session.commit()

    chunk_ready = RerankedSearchResult(
        point_id="p-1",
        document_id=ready_doc_id,
        chunk_index=0,
        content="Valid chunk from ready doc",
        token_count=5,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=0.9,
        score=0.9,
        rank=1,
        patient_id=pat_id,
    )
    chunk_failed = RerankedSearchResult(
        point_id="p-2",
        document_id=failed_doc_id,
        chunk_index=0,
        content="Stale chunk from failed doc",
        token_count=5,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=0.85,
        score=0.85,
        rank=2,
        patient_id=pat_id,
    )
    chunk_deleted = RerankedSearchResult(
        point_id="p-3",
        document_id=deleted_doc_id,
        chunk_index=0,
        content="Stale chunk from deleted doc",
        token_count=5,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        rerank_score=0.80,
        score=0.80,
        rank=3,
        patient_id=pat_id,
    )

    mock_retriever = AsyncMock()
    mock_retriever.search.return_value = [chunk_ready, chunk_failed, chunk_deleted]

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-retrieval/search",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": pat_id,
                    "query": "test query",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            # Only the READY document chunk should be returned!
            assert len(data["results"]) == 1
            assert data["results"][0]["documentId"] == ready_doc_id
            assert data["results"][0]["content"] == "Valid chunk from ready doc"
    finally:
        app.dependency_overrides.pop(get_retriever, None)


@pytest.mark.asyncio
async def test_medical_retrieval_empty_results():
    """PART 28: Empty results returns [] cleanly."""
    mock_retriever = AsyncMock()
    mock_retriever.search.return_value = []

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-retrieval/search",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": "pat-none",
                    "query": "non-existent query",
                },
            )
            assert resp.status_code == 200
            assert resp.json() == {"results": []}
    finally:
        app.dependency_overrides.pop(get_retriever, None)
