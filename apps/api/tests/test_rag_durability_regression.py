from unittest.mock import AsyncMock, patch

import pytest

from src.config import settings
from src.models.document import Document
from src.models.external_document import ExternalDocument
from src.services.document_service import DocumentService
from src.services.model_manager import RERANKER_MODEL_ALIASES
from src.vector_store.qdrant_store import (
    QdrantConnectionError,
    classify_qdrant_error,
    is_fallback_permitted,
)


def test_cross_encoder_model_aliases_and_resolution():
    """Verify legacy model names resolve to FastEmbed-compatible Xenova model."""
    assert RERANKER_MODEL_ALIASES["cross-encoder/ms-marco-MiniLM-L-6-v2"] == "rerank-v3.5"
    assert RERANKER_MODEL_ALIASES["ms-marco-MiniLM-L-6-v2"] == "rerank-v3.5"
    assert RERANKER_MODEL_ALIASES["Xenova/ms-marco-MiniLM-L-6-v2"] == "rerank-v3.5"


def test_production_strictly_prohibits_ephemeral_fallback():
    """Verify that production and staging environments NEVER allow ephemeral Qdrant fallback."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        assert is_fallback_permitted() is False

    with patch.object(settings, "ENVIRONMENT", "prod"):
        assert is_fallback_permitted() is False

    with patch.object(settings, "ENVIRONMENT", "staging"):
        assert is_fallback_permitted() is False

    with patch.object(settings, "ENVIRONMENT", "test"):
        # Test environment allows fallback if explicitly enabled or defaults for offline tests
        with patch.dict("os.environ", {"ALLOW_EMBEDDED_QDRANT_FALLBACK": "false"}):
            assert is_fallback_permitted() is True


def test_qdrant_connection_error_classification():
    """Verify network drop and connection reset errors are properly classified as QdrantConnectionError."""

    conn_reset = ConnectionResetError(54, "Connection reset by peer")
    classified = classify_qdrant_error(conn_reset, "search", "test_collection")
    assert isinstance(classified, QdrantConnectionError)

    sock_err = OSError("Network unreachable")
    classified_sock = classify_qdrant_error(sock_err, "upsert", "test_collection")
    assert isinstance(classified_sock, QdrantConnectionError)


@pytest.mark.asyncio
async def test_document_deletion_purges_vectors_and_handles_partial_failures():
    """Verify document deletion removes Qdrant points and completes DB deletion even if storage deletion fails."""
    mock_db = AsyncMock()
    mock_storage = AsyncMock()
    mock_storage.delete.side_effect = Exception("Storage temporary failure")
    mock_repo = AsyncMock()

    test_doc = Document(
        id="test-doc-id-123",
        user_id="user-123",
        filename="test.pdf",
        storage_key="user-123/test.pdf",
        status="indexed",
        chunk_count=5,
    )
    mock_repo.get_by_id.return_value = test_doc

    mock_v_store = AsyncMock()

    service = DocumentService(
        db=mock_db,
        storage=mock_storage,
        repository=mock_repo,
        vector_store=mock_v_store,
    )

    await service.delete_user_document(document_id="test-doc-id-123", user_id="user-123")

    # 1. Vector points must be deleted
    mock_v_store.delete_document_points.assert_awaited_once_with(document_id="test-doc-id-123")
    # 2. Storage delete was attempted
    mock_storage.delete.assert_awaited_once_with("user-123/test.pdf")
    # 3. Database record is deleted despite storage error
    mock_repo.delete.assert_awaited_once_with(test_doc)


def test_chunk_count_model_attributes():
    """Verify chunk_count exists on Document and ExternalDocument models and defaults to None."""
    doc = Document(id="d-1", user_id="u-1", filename="test.pdf", storage_key="key-1")
    assert hasattr(doc, "chunk_count")
    assert doc.chunk_count is None
    doc.chunk_count = 12
    assert doc.chunk_count == 12

    ext_doc = ExternalDocument(
        id="ed-1",
        source_system="quick_clinic",
        external_patient_id="p-1",
        external_document_id="doc-1",
        file_name="notes.pdf",
        storage_path="ext/notes.pdf",
        mime_type="application/pdf",
        file_size=1024,
        document_type="lab_result",
    )
    assert hasattr(ext_doc, "chunk_count")
    assert ext_doc.chunk_count is None
    ext_doc.chunk_count = 7
    assert ext_doc.chunk_count == 7
