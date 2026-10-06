import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from qdrant_client import models
from sqlalchemy import select

from src.db import AsyncSessionLocal
from src.models.external_document import ExternalDocument
from src.models.external_document_content import ExternalDocumentContent
from src.processing.chunking import DocumentChunker, SemanticSplitter
from src.processing.cleaning import TextCleaner
from src.processing.extraction import DocumentExtractor
from src.storage.object_storage import ObjectStorage
from src.tasks.document_tasks import _process_medical_document
from src.vector_store import QdrantVectorStore


@pytest.fixture
def mock_storage() -> MagicMock:
    storage = MagicMock(spec=ObjectStorage)
    storage.download = AsyncMock()
    return storage


@pytest.fixture(autouse=True)
async def isolated_medical_database(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from src.db import Base
    from src.tasks import document_tasks

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'medical.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(document_tasks, "AsyncSessionLocal", sessions)
    monkeypatch.setitem(globals(), "AsyncSessionLocal", sessions)
    try:
        yield
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_process_medical_document_pdf_success(
    sample_pdf_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
    mock_semantic_embedder: MagicMock,
    mock_dense_embedder: MagicMock,
    mock_sparse_embedder: MagicMock,
    mock_vector_store: MagicMock,
    mock_storage: MagicMock,
):
    doc_id = f"doc-worker-pdf-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-worker-{uuid.uuid4().hex[:8]}"
    storage_path = f"medical-documents/{patient_id}/{doc_id}/report.pdf"

    # Seed ExternalDocument in DB
    async with AsyncSessionLocal() as session:
        doc = ExternalDocument(
            source_system="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_id,
            collection_id=f"patient_{hashlib.sha256(patient_id.encode()).hexdigest()[:32]}_records",
            external_document_id=doc_id,
            external_patient_id=patient_id,
            storage_path=storage_path,
            file_name="report.pdf",
            mime_type="application/pdf",
            file_size=len(sample_pdf_bytes),
            document_type="LAB_REPORT",
            report_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
            status="QUEUED",
        )
        session.add(doc)
        await session.commit()

    mock_storage.download.return_value = sample_pdf_bytes

    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=mock_image_captioner,
    )
    cleaner = TextCleaner()
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )

    embedded_doc = await _process_medical_document(
        document_id=doc_id,
        storage=mock_storage,
        extractor=extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=mock_dense_embedder,
        sparse_embedder=mock_sparse_embedder,
        vector_store=mock_vector_store,
    )

    assert embedded_doc is not None
    assert embedded_doc.total_chunks() >= 1

    # Verify storage download was called with proper path
    mock_storage.download.assert_awaited_once_with(storage_path)

    # Verify vector store was called with patient isolation payload
    mock_vector_store.index_document.assert_awaited_once()
    call_args, call_kwargs = mock_vector_store.index_document.call_args
    assert call_args[0] == doc_id
    extra_payload = call_kwargs["extra_payload"]
    assert extra_payload["client_id"] == "quick_clinic"
    assert extra_payload["tenant_id"] == "quick_clinic_default"
    assert extra_payload["owner_subject_id"] == patient_id
    assert extra_payload["collection_id"] == f"patient_{hashlib.sha256(patient_id.encode()).hexdigest()[:32]}_records"
    assert extra_payload["source_system"] == "quick_clinic"
    assert extra_payload["patient_id"] == patient_id
    assert extra_payload["document_id"] == doc_id
    assert extra_payload["document_type"] == "LAB_REPORT"
    assert extra_payload["file_name"] == "report.pdf"

    # Verify DB status updated to READY and ExtractedDocumentContent saved
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id)
        )
        saved_doc = res.scalar_one()
        assert saved_doc.status == "READY"
        assert saved_doc.processed_at is not None
        assert saved_doc.processing_error is None

        res_content = await session.execute(
            select(ExternalDocumentContent).where(
                ExternalDocumentContent.external_document_id == saved_doc.id
            )
        )
        content_row = res_content.scalar_one_or_none()
        assert content_row is not None
        assert "Search Sphere Document Extraction" in content_row.raw_text
        assert content_row.character_count > 0


@pytest.mark.asyncio
async def test_process_medical_document_image_ocr(
    sample_image_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_semantic_embedder: MagicMock,
    mock_dense_embedder: MagicMock,
    mock_sparse_embedder: MagicMock,
    mock_vector_store: MagicMock,
    mock_storage: MagicMock,
):
    doc_id = f"doc-worker-img-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-worker-img-{uuid.uuid4().hex[:8]}"
    storage_path = f"medical-documents/{patient_id}/{doc_id}/prescription.png"

    # Seed ExternalDocument in DB
    async with AsyncSessionLocal() as session:
        doc = ExternalDocument(
            source_system="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_id,
            collection_id=f"patient_{hashlib.sha256(patient_id.encode()).hexdigest()[:32]}_records",
            external_document_id=doc_id,
            external_patient_id=patient_id,
            storage_path=storage_path,
            file_name="prescription.png",
            mime_type="image/png",
            file_size=len(sample_image_bytes),
            document_type="PRESCRIPTION",
            status="QUEUED",
        )
        session.add(doc)
        await session.commit()

    mock_storage.download.return_value = sample_image_bytes
    mock_ocr_processor.extract_text.return_value = "Amoxicillin 500mg TID for 7 days"

    # Verify DocumentExtractor.extract_image routes properly
    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=MagicMock(),  # Captioner should NOT be invoked for medical image
    )
    cleaner = TextCleaner()
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )

    embedded_doc = await _process_medical_document(
        document_id=doc_id,
        storage=mock_storage,
        extractor=extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=mock_dense_embedder,
        sparse_embedder=mock_sparse_embedder,
        vector_store=mock_vector_store,
    )

    assert embedded_doc is not None
    # Verify DB content has OCR text
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id)
        )
        saved_doc = res.scalar_one()
        assert saved_doc.status == "READY"

        res_content = await session.execute(
            select(ExternalDocumentContent).where(
                ExternalDocumentContent.external_document_id == saved_doc.id
            )
        )
        content_row = res_content.scalar_one()
        assert "Amoxicillin 500mg TID" in content_row.raw_text


@pytest.mark.asyncio
async def test_process_medical_document_storage_error_marks_failed(
    mock_vector_store: MagicMock,
    mock_storage: MagicMock,
):
    doc_id = f"doc-fail-{uuid.uuid4().hex[:8]}"
    patient_id = f"pat-fail-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        doc = ExternalDocument(
            source_system="quick_clinic",
            tenant_id="quick_clinic_default",
            owner_subject_id=patient_id,
            collection_id=f"patient_{hashlib.sha256(patient_id.encode()).hexdigest()[:32]}_records",
            external_document_id=doc_id,
            external_patient_id=patient_id,
            storage_path=f"medical-documents/{patient_id}/{doc_id}/missing.pdf",
            file_name="missing.pdf",
            mime_type="application/pdf",
            file_size=1000,
            document_type="OTHER",
            status="QUEUED",
        )
        session.add(doc)
        await session.commit()

    mock_storage.download.side_effect = RuntimeError("Supabase storage download timeout")

    with pytest.raises(RuntimeError, match="Supabase storage download timeout"):
        await _process_medical_document(
            document_id=doc_id,
            storage=mock_storage,
            vector_store=mock_vector_store,
        )

    # Check status was marked FAILED in DB
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ExternalDocument).where(ExternalDocument.external_document_id == doc_id)
        )
        saved_doc = res.scalar_one()
        assert saved_doc.status == "FAILED"
        assert "Supabase storage download timeout" in saved_doc.processing_error


@pytest.mark.asyncio
async def test_qdrant_patient_isolation_search_filtering():
    """Verify that QdrantVectorStore search applies patient_id and source_system filter."""
    store = QdrantVectorStore()
    mock_client = AsyncMock()
    store.client = mock_client
    store._collection_ensured = True
    store.vector_dimension = 4

    # Mock query_points return
    mock_point = MagicMock()
    mock_point.id = "p-1"
    mock_point.score = 0.95
    mock_point.payload = {
        "document_id": "doc-isolated",
        "chunk_index": 0,
        "content": "Medical note",
        "token_count": 5,
        "start_page": 1,
        "end_page": 1,
        "page_numbers": [1],
        "block_types": ["text"],
        "patient_id": "patient-allowed",
        "source_system": "quick_clinic",
    }
    mock_response = MagicMock()
    mock_response.points = [mock_point]
    mock_client.query_points.return_value = mock_response

    results = await store.search_dense(
        query_vector=[0.1, 0.2, 0.3, 0.4],
        limit=5,
        patient_id="patient-allowed",
        source_system="quick_clinic",
    )

    assert len(results) == 1
    assert results[0].patient_id == "patient-allowed"
    assert results[0].source_system == "quick_clinic"

    # Verify filter passed to Qdrant query_points
    call_args, call_kwargs = mock_client.query_points.call_args
    query_filter = call_kwargs["query_filter"]
    assert query_filter is not None
    field_keys = [c.key for c in query_filter.must]
    assert "patient_id" in field_keys
    assert "source_system" in field_keys
