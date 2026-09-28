from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.document_extractor import (
    DocumentExtractionError,
    DocumentExtractor,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)
from src.processing.text_cleaner import (
    CleanedBlock,
    CleanedDocument,
    TextCleaner,
)
from src.services.document_fetcher import DocumentFetcher, FetchedDocument
from src.tasks.document_tasks import _process_document


@pytest.mark.asyncio
async def test_process_document_success_sequence() -> None:
    doc_id = "doc-test-123"
    fake_pdf = b"%PDF-1.4 test document"

    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=fake_pdf,
        )
    )

    page1_text = (
        "Architecture\u00a0Overview\n"
        "-----------------------\n"
        "This docu-\n"
        "ment explains system flow."
    )

    page1 = ExtractedPage(
        page_number=1,
        blocks=[
            ExtractedBlock(
                block_type="text",
                content=page1_text,
            ),
            ExtractedBlock(
                block_type="image",
                content=(
                    "[Image text: Diagram   V1]\n\n"
                    "[Image description: Architecture   workflow   chart]"
                ),
            ),
        ],
    )
    extracted_doc = ExtractedDocument(pages=[page1])

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.return_value = extracted_doc

    cleaner = TextCleaner()

    cleaned_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
    )

    # 1. Fetcher called with document_id
    mock_fetcher.fetch.assert_awaited_once_with(doc_id)

    # 2. Extractor called with downloaded bytes
    mock_extractor.extract.assert_called_once_with(fake_pdf)

    # 3. Cleaned document produced
    assert isinstance(cleaned_doc, CleanedDocument)
    assert len(cleaned_doc.pages) == 1
    p1 = cleaned_doc.pages[0]
    assert p1.page_number == 1
    assert len(p1.blocks) == 2

    # 4. Text block cleaned (Unicode normalized, hyphen repaired, divider stripped)
    text_block = p1.blocks[0]
    assert text_block.block_type == "text"
    assert "Architecture Overview" in text_block.content
    assert "This document explains" in text_block.content
    assert "-----------------------" not in text_block.content

    # 5. Image markers survived cleaning and order preserved
    image_block = p1.blocks[1]
    assert image_block.block_type == "image"
    assert "[Image text: Diagram V1]" in image_block.content
    assert "[Image description: Architecture workflow chart]" in image_block.content


@pytest.mark.asyncio
async def test_process_document_handles_empty_and_noisy_blocks() -> None:
    doc_id = "doc-noisy-456"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=b"pdf",
        )
    )

    page = ExtractedPage(
        page_number=1,
        blocks=[
            ExtractedBlock(
                block_type="text",
                content="====================",  # Pure noise
            ),
            ExtractedBlock(
                block_type="text",
                content="   \n\n   ",  # Pure empty whitespace
            ),
            ExtractedBlock(
                block_type="text",
                content="Meaningful content line.",
            ),
        ],
    )
    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.return_value = ExtractedDocument(pages=[page])

    cleaner = TextCleaner()
    cleaned_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
    )

    # Only meaningful block remains; noise and empty blocks pruned
    assert len(cleaned_doc.pages[0].blocks) == 1
    assert cleaned_doc.pages[0].blocks[0].content == "Meaningful content line."


@pytest.mark.asyncio
async def test_process_document_extraction_error_propagates() -> None:
    doc_id = "doc-failing-789"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=b"corrupt-data",
        )
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.side_effect = DocumentExtractionError("Corrupted PDF stream")

    mock_cleaner = MagicMock(spec=TextCleaner)

    with pytest.raises(DocumentExtractionError, match="Corrupted PDF stream"):
        await _process_document(
            document_id=doc_id,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
        )

    # Cleaner should not have been called if extraction failed
    mock_cleaner.clean_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_fetch_error_propagates() -> None:
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        side_effect=ValueError("Document not found: missing-id")
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_cleaner = MagicMock(spec=TextCleaner)

    with pytest.raises(ValueError, match="Document not found"):
        await _process_document(
            document_id="missing-id",
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
        )

    mock_extractor.extract.assert_not_called()
    mock_cleaner.clean_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_end_to_end_pipeline(
    sample_pdf_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
) -> None:
    """
    Verifies the real DocumentExtractor -> TextCleaner sequence with mocked storage/db.
    """
    doc_id = "doc-e2e-real"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=sample_pdf_bytes,
        )
    )

    extractor = DocumentExtractor(
        ocr_processor=mock_ocr_processor,
        image_captioner=mock_image_captioner,
    )
    cleaner = TextCleaner()

    cleaned_document = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=extractor,
        cleaner=cleaner,
    )

    assert isinstance(cleaned_document, CleanedDocument)
    assert len(cleaned_document.pages) == 1

    combined = cleaned_document.combined_text()
    assert "Search Sphere Document Extraction" in combined
    assert "This is native text extracted from PDF." in combined

    # Pipeline output remains structured CleanedDocument, no chunking/embedding yet
    assert hasattr(cleaned_document, "pages")
    assert all(isinstance(b, CleanedBlock) for b in cleaned_document.pages[0].blocks)
