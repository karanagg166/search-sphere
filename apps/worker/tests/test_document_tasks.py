from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.chunking import DocumentChunker, SemanticSplitter
from src.processing.cleaning import TextCleaner
from src.processing.embedding import DenseEmbedder, DenseEmbeddingError
from src.processing.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
)
from src.processing.models.document import (
    EmbeddedChunk,
    EmbeddedDocument,
    ExtractedBlock,
    ExtractedDocument,
    ExtractedPage,
)
from src.services.document_fetcher import DocumentFetcher, FetchedDocument
from src.tasks.document_tasks import _process_document
from src.vector_store import QdrantVectorStore, QdrantVectorStoreError


@pytest.mark.asyncio
async def test_process_document_success_sequence(
    mock_semantic_embedder: MagicMock,
    mock_dense_embedder: MagicMock,
    mock_vector_store: MagicMock,
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

    embedded_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=mock_dense_embedder,
        vector_store=mock_vector_store,
    )

    # 1. Fetcher called with document_id
    mock_fetcher.fetch.assert_awaited_once_with(doc_id)

    # 2. Extractor called with downloaded bytes
    mock_extractor.extract.assert_called_once_with(fake_pdf)

    # 3. DenseEmbedder called once
    mock_dense_embedder.embed_document.assert_called_once()

    # 4. EmbeddedDocument produced
    assert isinstance(embedded_doc, EmbeddedDocument)
    assert embedded_doc.total_chunks() >= 1

    first_chunk = embedded_doc.chunks[0]
    assert isinstance(first_chunk, EmbeddedChunk)
    assert first_chunk.chunk_index == 0
    assert first_chunk.start_page == 1
    assert first_chunk.end_page == 1
    assert len(first_chunk.embedding) == 384

    # 5. Content cleaned and preserved in chunk
    assert "Architecture Overview" in first_chunk.content
    assert "This document explains" in first_chunk.content
    assert "-----------------------" not in first_chunk.content
    assert "[Image text: Diagram V1]" in first_chunk.content
    assert "[Image description: Architecture workflow chart]" in first_chunk.content

    # 6. Vector store indexed with produced EmbeddedDocument
    mock_vector_store.index_document.assert_awaited_once_with(doc_id, embedded_doc)


@pytest.mark.asyncio
async def test_process_document_handles_empty_and_noisy_blocks(
    mock_semantic_embedder: MagicMock,
    mock_dense_embedder: MagicMock,
    mock_vector_store: MagicMock,
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

    embedded_doc = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=mock_extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=mock_dense_embedder,
        vector_store=mock_vector_store,
    )

    # Only meaningful block remains and is embedded
    assert embedded_doc.total_chunks() == 1
    assert embedded_doc.chunks[0].content == "Meaningful content line."
    assert len(embedded_doc.chunks[0].embedding) == 384

    # Vector store indexing called
    mock_vector_store.index_document.assert_awaited_once_with(doc_id, embedded_doc)


@pytest.mark.asyncio
async def test_process_document_extraction_error_propagates(
    mock_vector_store: MagicMock,
) -> None:
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
    mock_embedder = MagicMock(spec=DenseEmbedder)

    with pytest.raises(DocumentExtractionError, match="Corrupted PDF stream"):
        await _process_document(
            document_id=doc_id,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

    # Downstream components should not have been called
    mock_cleaner.clean_document.assert_not_called()
    mock_embedder.embed_document.assert_not_called()
    mock_vector_store.index_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_chunking_error_skips_embedder(
    mock_vector_store: MagicMock,
) -> None:
    doc_id = "doc-chunk-fail"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=b"%PDF",
        )
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.return_value = ExtractedDocument(pages=[])

    mock_cleaner = MagicMock(spec=TextCleaner)
    mock_cleaner.clean_document.return_value = ExtractedDocument(pages=[])

    mock_chunker = MagicMock(spec=DocumentChunker)
    mock_chunker.chunk_document.side_effect = RuntimeError("Chunking boundary failed")

    mock_embedder = MagicMock(spec=DenseEmbedder)

    with pytest.raises(RuntimeError, match="Chunking boundary failed"):
        await _process_document(
            document_id=doc_id,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
            chunker=mock_chunker,
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

    # Embedder and vector store must NOT be called if chunking fails
    mock_embedder.embed_document.assert_not_called()
    mock_vector_store.index_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_embedding_error_propagates(
    mock_vector_store: MagicMock,
) -> None:
    doc_id = "doc-embed-fail"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=b"%PDF",
        )
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.return_value = ExtractedDocument(pages=[])

    mock_cleaner = MagicMock(spec=TextCleaner)
    mock_chunker = MagicMock(spec=DocumentChunker)
    mock_chunker.chunk_document.return_value = MagicMock()

    mock_embedder = MagicMock(spec=DenseEmbedder)
    mock_embedder.embed_document.side_effect = DenseEmbeddingError(
        "Model out of memory during encode"
    )

    with pytest.raises(DenseEmbeddingError, match="Model out of memory"):
        await _process_document(
            document_id=doc_id,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
            chunker=mock_chunker,
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

    mock_vector_store.index_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_fetch_error_propagates(
    mock_vector_store: MagicMock,
) -> None:
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        side_effect=ValueError("Document not found: missing-id")
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_cleaner = MagicMock(spec=TextCleaner)
    mock_embedder = MagicMock(spec=DenseEmbedder)

    with pytest.raises(ValueError, match="Document not found"):
        await _process_document(
            document_id="missing-id",
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
            embedder=mock_embedder,
            vector_store=mock_vector_store,
        )

    mock_extractor.extract.assert_not_called()
    mock_cleaner.clean_document.assert_not_called()
    mock_embedder.embed_document.assert_not_called()
    mock_vector_store.index_document.assert_not_called()


@pytest.mark.asyncio
async def test_process_document_vector_store_error_propagates(
    mock_dense_embedder: MagicMock,
) -> None:
    """
    If vector store indexing fails, worker must raise and not complete successfully.
    """
    doc_id = "doc-vec-fail"
    mock_fetcher = MagicMock(spec=DocumentFetcher)
    mock_fetcher.fetch = AsyncMock(
        return_value=FetchedDocument(
            id=doc_id,
            storage_key=f"documents/{doc_id}.pdf",
            status="uploaded",
            content=b"%PDF",
        )
    )

    mock_extractor = MagicMock(spec=DocumentExtractor)
    mock_extractor.extract.return_value = ExtractedDocument(pages=[])

    mock_cleaner = MagicMock(spec=TextCleaner)
    mock_cleaner.clean_document.return_value = ExtractedDocument(pages=[])

    mock_chunker = MagicMock(spec=DocumentChunker)
    mock_chunker.chunk_document.return_value = MagicMock()

    mock_vector_store = MagicMock(spec=QdrantVectorStore)
    mock_vector_store.index_document = AsyncMock(
        side_effect=QdrantVectorStoreError("Qdrant connection dropped during upsert")
    )

    with pytest.raises(QdrantVectorStoreError, match="Qdrant connection dropped"):
        await _process_document(
            document_id=doc_id,
            fetcher=mock_fetcher,
            extractor=mock_extractor,
            cleaner=mock_cleaner,
            chunker=mock_chunker,
            embedder=mock_dense_embedder,
            vector_store=mock_vector_store,
        )

    mock_vector_store.index_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_document_end_to_end_pipeline(
    sample_pdf_bytes: bytes,
    mock_ocr_processor: MagicMock,
    mock_image_captioner: MagicMock,
    mock_semantic_embedder: MagicMock,
    mock_dense_embedder: MagicMock,
    mock_vector_store: MagicMock,
) -> None:
    """
    Verifies the real pipeline sequence:
    DocumentExtractor -> TextCleaner -> DocumentChunker
    -> DenseEmbedder -> QdrantVectorStore
    with mocked storage/db.
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

    embedded_document = await _process_document(
        document_id=doc_id,
        fetcher=mock_fetcher,
        extractor=extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=mock_dense_embedder,
        vector_store=mock_vector_store,
    )

    assert isinstance(embedded_document, EmbeddedDocument)
    assert embedded_document.total_chunks() >= 1

    combined = " ".join(c.content for c in embedded_document.chunks)
    assert "Search Sphere Document Extraction" in combined
    assert "This is native text extracted from PDF." in combined

    # Pipeline output is structured EmbeddedDocument containing EmbeddedChunks
    assert all(isinstance(c, EmbeddedChunk) for c in embedded_document.chunks)
    assert embedded_document.chunks[0].chunk_index == 0
    assert embedded_document.chunks[0].start_page == 1
    assert len(embedded_document.chunks[0].embedding) == 384

    # Vector store must have been invoked with exact document_id and embedded_document
    mock_vector_store.index_document.assert_awaited_once_with(doc_id, embedded_document)
