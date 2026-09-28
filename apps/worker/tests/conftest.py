from io import BytesIO
from unittest.mock import MagicMock

import fitz
import pytest
from PIL import Image

from src.processing.image_captioner import ImageCaptioner
from src.processing.ocr_processor import OcrProcessor


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
