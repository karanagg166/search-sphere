from io import BytesIO
from typing import Any
from unittest.mock import MagicMock

import fitz
import pytest
from PIL import Image

from src.processing.extraction import ImageCaptioner, OcrProcessor


@pytest.fixture
def sample_image_bytes() -> bytes:
    """Creates a valid 150x150 PNG image in bytes."""
    img = Image.new("RGB", (150, 150), color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def tiny_image_bytes() -> bytes:
    """Creates a tiny 50x50 PNG image (should be filtered by _is_useful_image)."""
    img = Image.new("RGB", (50, 50), color="blue")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    """Creates an in-memory 1-page PDF document with sample text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 72), "Search Sphere Document Extraction")
    page.insert_text(fitz.Point(50, 100), "This is native text extracted from PDF.")
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def multipage_pdf_bytes() -> bytes:
    """Creates an in-memory 2-page PDF document."""
    doc = fitz.open()
    page1 = doc.new_page()
    page1.insert_text(fitz.Point(50, 72), "First Page Header")
    page1.insert_text(fitz.Point(50, 100), "First Page Body Content")

    page2 = doc.new_page()
    page2.insert_text(fitz.Point(50, 72), "Second Page Header")
    page2.insert_text(fitz.Point(50, 100), "Second Page Body Content")

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


@pytest.fixture
def mock_ocr_processor() -> MagicMock:
    """Provides a mocked OcrProcessor instance."""
    mock = MagicMock(spec=OcrProcessor)
    mock.extract_text.return_value = "Extracted OCR text from diagram"
    return mock


@pytest.fixture
def mock_image_captioner() -> MagicMock:
    """Provides a mocked ImageCaptioner instance."""
    mock = MagicMock(spec=ImageCaptioner)
    mock.describe.return_value = "A technical architectural diagram"
    return mock


@pytest.fixture
def mock_semantic_embedder() -> MagicMock:
    """
    Provides a mocked SemanticEmbedder instance returning deterministic embeddings.
    """
    from src.processing.chunking.semantic_splitter import SemanticEmbedder

    mock = MagicMock(spec=SemanticEmbedder)
    # Default: returns identical unit vectors so cosine distance is 0.0
    mock.embed.side_effect = lambda texts: [[1.0, 0.0, 0.0] for _ in texts]
    return mock


@pytest.fixture
def mock_dense_embedder() -> MagicMock:
    """
    Provides a mocked DenseEmbedder instance returning deterministic
    384-d normalized vectors.
    """

    from src.processing.embedding import DenseEmbedder
    from src.processing.models.document import EmbeddedChunk, EmbeddedDocument

    mock = MagicMock(spec=DenseEmbedder)
    mock.dimension = 384
    mock.model_name = "sentence-transformers/all-MiniLM-L6-v2"
    mock.batch_size = 32

    def _embed_document(chunked_doc: Any) -> EmbeddedDocument:
        chunks = []
        for c in chunked_doc.chunks:
            chunks.append(
                EmbeddedChunk(
                    chunk_index=c.chunk_index,
                    content=c.content,
                    token_count=c.token_count,
                    start_page=c.start_page,
                    end_page=c.end_page,
                    page_numbers=list(c.page_numbers),
                    block_types=list(c.block_types),
                    embedding=[0.05] * 384,
                )
            )
        return EmbeddedDocument(chunks=chunks)

    mock.embed_document.side_effect = _embed_document
    return mock


@pytest.fixture
def mock_vector_store() -> MagicMock:
    """
    Provides a mocked QdrantVectorStore instance returning success.
    """
    from unittest.mock import AsyncMock

    from src.vector_store import QdrantVectorStore

    mock = MagicMock(spec=QdrantVectorStore)
    mock.collection_name = "test_documents"
    mock.vector_dimension = 384
    mock.index_document = AsyncMock(return_value=1)
    mock.close = AsyncMock()
    return mock
