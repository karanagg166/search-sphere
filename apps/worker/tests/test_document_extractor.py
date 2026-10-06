from unittest.mock import MagicMock

import fitz
import pytest

from src.processing.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    ImageCaptioningError,
    OcrProcessingError,
)
from src.processing.models.document import (
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)


def test_extracted_block_and_page_combined_text() -> None:
    b1 = ExtractedBlock(block_type="text", content="Header Title")
    b2 = ExtractedBlock(block_type="image", content="[Image text: Diagram 1]")
    b3 = ExtractedBlock(block_type="text", content="   ")  # Empty whitespace block

    page = ExtractedPage(page_number=1, blocks=[b1, b2, b3])
    assert page.page_number == 1
    assert page.combined_text() == "Header Title\n\n[Image text: Diagram 1]"


def test_extracted_document_combined_text() -> None:
    p1 = ExtractedPage(
        page_number=1,
        blocks=[ExtractedBlock(block_type="text", content="Page 1 Text")],
    )
    p2 = ExtractedPage(
        page_number=2,
        blocks=[ExtractedBlock(block_type="text", content="Page 2 Text")],
    )
    doc = ExtractedDocument(pages=[p1, p2])

    assert len(doc.pages) == 2
    assert doc.combined_text() == "Page 1 Text\n\nPage 2 Text"


def test_extract_empty_pdf_raises_error() -> None:
    extractor = DocumentExtractor()
    with pytest.raises(
        DocumentExtractionError,
        match="Cannot extract content from an empty document",
    ):
        extractor.extract(b"")


def test_extract_corrupted_pdf_raises_error() -> None:
    extractor = DocumentExtractor()
    with pytest.raises(
        DocumentExtractionError,
        match="Failed to open PDF document",
    ):
        extractor.extract(b"Not a valid PDF file format")


def test_extract_single_page_pdf(sample_pdf_bytes: bytes) -> None:
    extractor = DocumentExtractor()
    extracted_doc = extractor.extract(sample_pdf_bytes)

    assert len(extracted_doc.pages) == 1
    page = extracted_doc.pages[0]
    assert page.page_number == 1
    assert len(page.blocks) >= 1
    assert "Search Sphere Document Extraction" in page.combined_text()
    assert "This is native text extracted from PDF." in page.combined_text()


def test_extract_multipage_pdf(multipage_pdf_bytes: bytes) -> None:
    extractor = DocumentExtractor()
    extracted_doc = extractor.extract(multipage_pdf_bytes)

    assert len(extracted_doc.pages) == 2
    assert extracted_doc.pages[0].page_number == 1
    assert "First Page Header" in extracted_doc.pages[0].combined_text()
    assert extracted_doc.pages[1].page_number == 2
    assert "Second Page Header" in extracted_doc.pages[1].combined_text()


def test_reading_order_key() -> None:
    extractor = DocumentExtractor()
    b1 = {"bbox": (10.0, 50.0, 100.0, 80.0)}
    b2 = {"bbox": (5.0, 20.0, 50.0, 40.0)}

    assert extractor._reading_order_key(b1) == (50.0, 10.0)
    assert extractor._reading_order_key(b2) == (20.0, 5.0)

    # b2 (y0=20.0) comes before b1 (y0=50.0)
    sorted_blocks = sorted([b1, b2], key=extractor._reading_order_key)
    assert sorted_blocks == [b2, b1]


def test_extract_text_block_spans() -> None:
    extractor = DocumentExtractor()
    block = {
        "lines": [
            {
                "spans": [
                    {"text": "Line 1 Part A "},
                    {"text": "Part B"},
                ]
            },
            {
                "spans": [
                    {"text": "Line 2 text"},
                ]
            },
        ]
    }
    extracted = extractor._extract_text_block(block)
    assert extracted == "Line 1 Part A Part B\nLine 2 text"


def test_is_useful_image_filtering(
    sample_image_bytes: bytes,
    tiny_image_bytes: bytes,
) -> None:
    extractor = DocumentExtractor()

    # 150x150 image is useful
    assert extractor._is_useful_image(sample_image_bytes) is True

    # 50x50 image is smaller than threshold (100x100) -> filtered out
    assert extractor._is_useful_image(tiny_image_bytes) is False

    # Corrupt bytes -> handled gracefully, returns False
    assert extractor._is_useful_image(b"invalid image bytes") is False


def test_extract_pdf_with_image_and_multimodal_enrichment(
    sample_image_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
) -> None:
    # Build a PDF containing both text and an embedded image
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 72), "Document Header")
    rect = fitz.Rect(50, 100, 250, 300)
    page.insert_image(rect, stream=sample_image_bytes)
    page.insert_text(fitz.Point(50, 350), "Document Footer")
    pdf_bytes = doc.tobytes()
    doc.close()

    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=mock_image_captioner,
    )
    result = extractor.extract(pdf_bytes)

    assert len(result.pages) == 1
    page_blocks = result.pages[0].blocks

    # Verify blocks contain text and image
    block_types = [b.block_type for b in page_blocks]
    assert "text" in block_types
    assert "image" in block_types

    combined = result.combined_text()
    assert "Document Header" in combined
    assert "[Image text: Extracted OCR text from diagram]" in combined
    assert "[Image description: A technical architectural diagram]" in combined
    assert "Document Footer" in combined

    mock_ocr_processor.extract_text.assert_called_once()
    mock_image_captioner.describe.assert_called_once()


def test_extract_pdf_image_ocr_failure_is_resilient(
    sample_image_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
) -> None:
    # Configure OCR to fail
    mock_ocr_processor.extract_text.side_effect = OcrProcessingError("OCR failure")

    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(50, 100, 250, 300)
    page.insert_image(rect, stream=sample_image_bytes)
    pdf_bytes = doc.tobytes()
    doc.close()

    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=mock_image_captioner,
    )
    result = extractor.extract(pdf_bytes)

    # Document extraction does not crash; contains description only
    combined = result.combined_text()
    assert "[Image description: A technical architectural diagram]" in combined
    assert "[Image text:" not in combined


def test_extract_pdf_image_caption_failure_is_resilient(
    sample_image_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
) -> None:
    # Configure captioner to fail
    mock_image_captioner.describe.side_effect = ImageCaptioningError("Caption failure")

    doc = fitz.open()
    page = doc.new_page()
    rect = fitz.Rect(50, 100, 250, 300)
    page.insert_image(rect, stream=sample_image_bytes)
    pdf_bytes = doc.tobytes()
    doc.close()

    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=mock_image_captioner,
    )
    result = extractor.extract(pdf_bytes)

    # Document extraction does not crash; contains OCR only
    combined = result.combined_text()
    assert "[Image text: Extracted OCR text from diagram]" in combined
    assert "[Image description:" not in combined


def test_extract_page_dict_failure_raises_error() -> None:
    extractor = DocumentExtractor()
    mock_page = MagicMock()
    mock_page.get_text.side_effect = RuntimeError("Fitz layout failure")

    with pytest.raises(
        DocumentExtractionError,
        match="Failed to inspect PDF page 1",
    ):
        extractor._extract_page(mock_page, page_number=1)
