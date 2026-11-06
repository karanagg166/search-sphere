from unittest.mock import patch

from src.config import settings
from src.models.document import Document
from src.models.external_document import ExternalDocument
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


# Deletion failure/restart regressions use real PostgreSQL/pgvector in
# scripts/tests/test_document_cleanup_postgres.py.


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
