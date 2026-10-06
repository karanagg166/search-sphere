from src.security.medical_context import patient_collection_id
from datetime import datetime, timezone
import json
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.config import settings
from src.db import AsyncSessionLocal
from src.main import app
from src.models.external_document import ExternalDocument
from src.processing.models.search import RerankedSearchResult
from src.schemas.search import RewriteResult
from src.services.answer_generator import (
    AnswerGenerator,
    MEDICAL_NO_RESULTS_ANSWER,
    get_answer_generator,
)
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever

TEST_SERVICE_SECRET = "test-service-secret"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer wrong-service-secret"}


@pytest.fixture(autouse=True)
def ensure_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured during tests."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


@pytest.fixture
async def sample_patient_documents():
    """Seed sample READY documents for Patient A and Patient B in PostgreSQL."""
    patient_a_id = f"pat-a-{uuid.uuid4().hex[:8]}"
    patient_b_id = f"pat-b-{uuid.uuid4().hex[:8]}"
    doc_a1_id = f"doc-a1-{uuid.uuid4().hex[:8]}"
    doc_b1_id = f"doc-b1-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        doc_a1 = ExternalDocument(
            source_system="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_a_id,
            collection_id=patient_collection_id(patient_a_id),
            external_patient_id=patient_a_id,
            external_document_id=doc_a1_id,
            file_name="bp_report_a.pdf",
            mime_type="application/pdf",
            file_size=1024,
            storage_path=f"medical-documents/{patient_a_id}/{doc_a1_id}/bp_report_a.pdf",
            document_type="LAB_REPORT",
            report_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
            status="READY",
        )
        doc_b1 = ExternalDocument(
            source_system="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_b_id,
            collection_id=patient_collection_id(patient_b_id),
            external_patient_id=patient_b_id,
            external_document_id=doc_b1_id,
            file_name="cardiac_panel_b.pdf",
            mime_type="application/pdf",
            file_size=2048,
            storage_path=f"medical-documents/{patient_b_id}/{doc_b1_id}/cardiac_panel_b.pdf",
            document_type="LAB_REPORT",
            report_date=datetime(2026, 10, 3, tzinfo=timezone.utc),
            status="READY",
        )
        session.add_all([doc_a1, doc_b1])
        await session.commit()

    yield {
        "patient_a_id": patient_a_id,
        "patient_b_id": patient_b_id,
        "doc_a1_id": doc_a1_id,
        "doc_b1_id": doc_b1_id,
    }

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ExternalDocument).where(
                ExternalDocument.external_document_id.in_([doc_a1_id, doc_b1_id])
            )
        )
        docs = res.scalars().all()
        for doc in docs:
            await session.delete(doc)
        await session.commit()


@pytest.mark.asyncio
async def test_medical_chat_auth_missing():
    """Search Sphere tests: missing service secret -> denied 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-rag/chat",
            json={
                "patientId": "pat-123",
                "message": "What were the recent BP readings?",
            },
        )
        assert resp.status_code == 401
        assert "Authorization header is required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_medical_chat_auth_invalid():
    """Search Sphere tests: wrong service secret -> denied 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-rag/chat",
            headers=INVALID_AUTH_HEADER,
            json={
                "patientId": "pat-123",
                "message": "What were the recent BP readings?",
            },
        )
        assert resp.status_code == 401
        assert "Invalid service secret" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_medical_chat_multi_turn_query_rewriting(sample_patient_documents):
    """
    Search Sphere tests: Multi-turn query rewriting
    History: 'What was the BP?'
    Follow-up: 'What about the later one?'
    Verify standalone retrieval query references later BP reading.
    """
    data = sample_patient_documents
    patient_a = data["patient_a_id"]
    doc_a1 = data["doc_a1_id"]

    mock_retriever = AsyncMock()
    mock_retriever.search.return_value = [
        RerankedSearchResult(
            point_id="pt-1",
            document_id=doc_a1,
            client_id="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_a,
            collection_id=patient_collection_id(patient_a),
            patient_id=patient_a,
            content="Oct 3 follow-up: Blood pressure 125/82 mmHg.",
            score=0.95,
            rerank_score=0.95,
            chunk_index=0,
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            file_name="bp_report_a.pdf",
            document_type="LAB_REPORT",
            report_date="2026-10-01T00:00:00Z",
        )
    ]

    mock_rewriter = AsyncMock()
    mock_rewriter.rewrite.return_value = RewriteResult(
        original_query="What about the later one?",
        retrieval_query="later blood pressure reading in patient medical reports",
        rewritten=True,
        reason="Resolved pronoun to blood pressure from context",
    )

    mock_generator = AsyncMock()
    mock_generator.generate_answer.return_value = (
        "The later reading documented blood pressure at 125/82 mmHg [1].",
        mock_retriever.search.return_value,
    )

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "What about the later one?",
                    "history": [
                        {"role": "user", "content": "What was the BP?"},
                        {"role": "assistant", "content": "120/80 on Oct 1 [1]."},
                    ],
                },
            )
            assert resp.status_code == 200
            body = resp.json()

            # Verify query rewriter was called with history
            assert mock_rewriter.rewrite.called
            call_kwargs = mock_rewriter.rewrite.call_args.kwargs
            assert call_kwargs["query"] == "What about the later one?"
            assert len(call_kwargs["conversation_context"]) == 2

            # Verify retriever was called with the REWRITTEN query
            assert mock_retriever.search.called
            retrieval_query_used = mock_retriever.search.call_args.kwargs["query"]
            assert retrieval_query_used == "later blood pressure reading in patient medical reports"

            # Verify patient filter
            filters = mock_retriever.search.call_args.kwargs["filters"]
            assert filters["source_system"] == "quick_clinic"
            assert filters["patient_id"] == patient_a

            # Verify response
            assert "125/82 mmHg [1]" in body["answer"]
            assert body["rewritten"] is True
            assert body["retrievalQuery"] == "later blood pressure reading in patient medical reports"
            assert len(body["citations"]) == 1
            assert body["citations"][0]["documentId"] == doc_a1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_medical_chat_patient_filter_and_contamination_defense(sample_patient_documents):
    """
    Search Sphere tests: Patient filter and History contamination defense
    History mentions Patient B facts ('Patient B BP was 180/110').
    Current conversation is for Patient A.
    Verify:
    1. Retrieval filter strictly enforces patient_id=Patient A.
    2. Any chunk belonging to Patient B is rejected and never returned.
    """
    data = sample_patient_documents
    patient_a = data["patient_a_id"]
    patient_b = data["patient_b_id"]
    doc_a1 = data["doc_a1_id"]
    doc_b1 = data["doc_b1_id"]

    mock_retriever = AsyncMock()
    # Simulate accidental or adversarial cross-patient chunk returned from retrieval
    mock_retriever.search.return_value = [
        RerankedSearchResult(
            point_id="pt-a",
            document_id=doc_a1,
            client_id="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_a,
            collection_id=patient_collection_id(patient_a),
            patient_id=patient_a,
            content="Patient A BP: 120/80 mmHg.",
            score=0.90,
            rerank_score=0.90,
            chunk_index=0,
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
        ),
        RerankedSearchResult(
            point_id="pt-b",
            document_id=doc_b1,
            client_id="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_b,
            collection_id=patient_collection_id(patient_b),
            patient_id=patient_b,
            content="Patient B BP: 180/110 mmHg.",
            score=0.95,
            rerank_score=0.95,
            chunk_index=0,
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
        ),
    ]

    mock_generator = AsyncMock()
    # Generator receives only sanitized chunks
    async def mock_gen(query, chunks, **kwargs):
        # Verify chunks received by generator contain ONLY Patient A chunks
        for c in chunks:
            assert c.document_id != doc_b1
        return "Patient A blood pressure is 120/80 mmHg [1].", chunks

    mock_generator.generate_answer.side_effect = mock_gen

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "What about the other patient's value?",
                    "history": [
                        {"role": "user", "content": "Patient B BP was 180/110"},
                    ],
                },
            )
            assert resp.status_code == 200
            body = resp.json()

            # Verify Qdrant filter had Patient A ONLY
            filters = mock_retriever.search.call_args.kwargs["filters"]
            assert filters["patient_id"] == patient_a
            assert filters["source_system"] == "quick_clinic"

            # Verify only Patient A document was cited
            assert len(body["citations"]) == 1
            assert body["citations"][0]["documentId"] == doc_a1
            assert "180/110" not in body["answer"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_medical_chat_fresh_grounding(sample_patient_documents):
    """
    Search Sphere tests: Fresh grounding
    Every follow-up query MUST trigger fresh retrieval.
    """
    data = sample_patient_documents
    patient_a = data["patient_a_id"]
    doc_a1 = data["doc_a1_id"]

    mock_retriever = AsyncMock()
    mock_retriever.search.return_value = [
        RerankedSearchResult(
            point_id="pt-a",
            document_id=doc_a1,
            client_id="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_a,
            collection_id=patient_collection_id(patient_a),
            patient_id=patient_a,
            content="Freshly retrieved report: Blood pressure is 120/80.",
            score=0.91,
            rerank_score=0.91,
            chunk_index=0,
            token_count=10,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
        )
    ]

    mock_generator = AsyncMock()
    mock_generator.generate_answer.return_value = (
        "Fresh retrieval shows BP 120/80 [1].",
        mock_retriever.search.return_value,
    )

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "Can you check the pressure again?",
                    "history": [
                        {"role": "user", "content": "What was the BP?"},
                        {"role": "assistant", "content": "120/80 on Oct 1 [1]."},
                    ],
                },
            )
            assert resp.status_code == 200
            # Verify retrieval was invoked freshly
            assert mock_retriever.search.called
            assert mock_retriever.search.call_count == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_medical_chat_streaming_sse_flow(sample_patient_documents):
    """
    Search Sphere tests: Streaming endpoint
    Verifies SSE format:
    - token events emitted
    - citations event emitted
    - done event emitted
    """
    data = sample_patient_documents
    patient_a = data["patient_a_id"]
    doc_a1 = data["doc_a1_id"]

    mock_retriever = AsyncMock()
    chunk = RerankedSearchResult(
        point_id="pt-a",
        document_id=doc_a1,
        client_id="quick_clinic",
        tenant_id="quick_clinic_default",
        owner_subject_id=patient_a,
        collection_id=patient_collection_id(patient_a),
        patient_id=patient_a,
        content="Lab report shows glucose 95 mg/dL.",
        score=0.92,
        rerank_score=0.92,
        chunk_index=0,
        token_count=10,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
        file_name="glucose_report.pdf",
        document_type="LAB_REPORT",
        report_date="2026-10-01T00:00:00Z",
    )
    mock_retriever.search.return_value = [chunk]

    async def mock_token_stream():
        tokens = ["The ", "patient's ", "fasting ", "glucose ", "is ", "95 mg/dL ", "[1]."]
        for t in tokens:
            yield t

    mock_generator = AsyncMock()
    mock_generator.generate_answer_stream.return_value = (mock_token_stream(), [chunk])

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat/stream",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "What is the glucose reading?",
                },
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]

            content = resp.text
            # Check SSE structure
            assert "event: token" in content
            assert "event: citations" in content
            assert "event: done" in content

            # Verify citations payload in citations event
            lines = content.split("\n")
            citation_events = [lines[i + 1] for i, l in enumerate(lines) if l == "event: citations"]
            assert len(citation_events) > 0
            citation_data = json.loads(citation_events[0].replace("data: ", ""))
            assert len(citation_data["citations"]) == 1
            assert citation_data["citations"][0]["documentId"] == doc_a1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_medical_chat_streaming_error_handling(sample_patient_documents):
    """
    Search Sphere tests: Streaming error handling
    When generation raises an unexpected exception, stream emits error event.
    """
    data = sample_patient_documents
    patient_a = data["patient_a_id"]
    doc_a1 = data["doc_a1_id"]

    mock_retriever = AsyncMock()
    chunk = RerankedSearchResult(
        point_id="pt-a",
        document_id=doc_a1,
        client_id="quick_clinic",
        tenant_id="quick_clinic_default",
        owner_subject_id=patient_a,
        collection_id=patient_collection_id(patient_a),
        patient_id=patient_a,
        content="Lab report.",
        score=0.92,
        rerank_score=0.92,
        chunk_index=0,
        token_count=10,
        start_page=1,
        end_page=1,
        page_numbers=[1],
        block_types=["text"],
    )
    mock_retriever.search.return_value = [chunk]

    mock_generator = AsyncMock()
    mock_generator.generate_answer_stream.side_effect = RuntimeError("Provider failure")

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat/stream",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "What is the reading?",
                },
            )
            assert resp.status_code == 200
            content = resp.text
            assert "event: error" in content
            assert "Unable to generate answer" in content
    finally:
        app.dependency_overrides.clear()
