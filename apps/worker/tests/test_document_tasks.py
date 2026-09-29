from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.chunking import DocumentChunker, SemanticSplitter
from src.processing.cleaning import TextCleaner
from src.processing.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
)
from src.processing.models.document import (
    ChunkedDocument,
    DocumentChunk,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)
from src.services.document_fetcher import DocumentFetcher, FetchedDocument
from src.tasks.document_tasks import _process_document


@pytest.mark.asyncio
async def test_process_document_success_sequence(
    mock_semantic_embedder: MagicMock,
) -> None:
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
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )

    chunked_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
        chunker=chunker,
    )

    # 1. Fetcher called with document_id
    mock_fetcher.fetch.assert_awaited_once_with(doc_id)

    # 2. Extractor called with downloaded bytes
    mock_extractor.extract.assert_called_once_with(fake_pdf)

    # 3. Chunked document produced
    assert isinstance(chunked_doc, ChunkedDocument)
    assert chunked_doc.total_chunks() >= 1

    first_chunk = chunked_doc.chunks[0]
    assert first_chunk.chunk_index == 0
    assert first_chunk.start_page == 1
    assert first_chunk.end_page == 1

    # 4. Content cleaned and preserved in chunk
    assert "Architecture Overview" in first_chunk.content
    assert "This document explains" in first_chunk.content
    assert "-----------------------" not in first_chunk.content
    assert "[Image text: Diagram V1]" in first_chunk.content
    assert "[Image description: Architecture workflow chart]" in first_chunk.content


@pytest.mark.asyncio
async def test_process_document_handles_empty_and_noisy_blocks(
    mock_semantic_embedder: MagicMock,
) -> None:
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
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )

    chunked_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
        chunker=chunker,
    )

    # Only meaningful block remains and is chunked
    assert chunked_doc.total_chunks() == 1
    assert chunked_doc.chunks[0].content == "Meaningful content line."


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
    mock_semantic_embedder: MagicMock,
) -> None:
    """
    Verifies the real DocumentExtractor -> TextCleaner -> DocumentChunker
    sequence with mocked storage/db.
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
    chunker = DocumentChunker(
        semantic_splitter=SemanticSplitter(embedder=mock_semantic_embedder)
    )

    chunked_document = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=extractor,
        cleaner=cleaner,
        chunker=chunker,
    )

    assert isinstance(chunked_document, ChunkedDocument)
    assert chunked_document.total_chunks() >= 1

    combined = " ".join(c.content for c in chunked_document.chunks)
    assert "Search Sphere Document Extraction" in combined
    assert "This is native text extracted from PDF." in combined

    # Pipeline output is structured ChunkedDocument
    assert all(isinstance(c, DocumentChunk) for c in chunked_document.chunks)
    assert chunked_document.chunks[0].chunk_index == 0
    assert chunked_document.chunks[0].start_page == 1
