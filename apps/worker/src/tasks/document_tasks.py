import asyncio

import dramatiq
import structlog

from src.processing.document_extractor import (
    DocumentExtractionError,
    DocumentExtractor,
)
from src.processing.text_cleaner import CleanedDocument, TextCleaner
from src.services.document_fetcher import DocumentFetcher

logger = structlog.get_logger()


@dramatiq.actor(
    queue_name="default",
    actor_name="process_document_task",
    max_retries=3,
)
def process_document_task(document_id: str) -> None:
    """
    Background job responsible for processing a single document.

    Current pipeline stages:
    1. Load document metadata from PostgreSQL.
    2. Download original PDF from object storage.
    3. Extract document content (native text, OCR text, and BLIP image captions).
    4. Deterministically clean and normalize document text while preserving structure.

    Chunking, embeddings, and vector storage are handled in later pipeline stages.
    """
    asyncio.run(_process_document(document_id))


async def _process_document(
    document_id: str,
    fetcher: DocumentFetcher | None = None,
    extractor: DocumentExtractor | None = None,
    cleaner: TextCleaner | None = None,
) -> CleanedDocument:
    logger.info(
        "Document processing started",
        document_id=document_id,
    )

    doc_fetcher = fetcher or DocumentFetcher()
    doc_extractor = extractor or DocumentExtractor()
    doc_cleaner = cleaner or TextCleaner()

    try:
        fetched_document = await doc_fetcher.fetch(document_id)

        logger.info(
            "Document fetched successfully",
            document_id=document_id,
            storage_key=fetched_document.storage_key,
            size=len(fetched_document.content),
        )

        extracted_document = doc_extractor.extract(fetched_document.content)

        raw_text = extracted_document.combined_text()

        logger.info(
            "Document extraction completed",
            document_id=document_id,
            pages=len(extracted_document.pages),
            extracted_characters=len(raw_text),
            preview=raw_text[:500],
        )

        cleaned_document = doc_cleaner.clean_document(extracted_document)

        cleaned_text = cleaned_document.combined_text()

        logger.info(
            "Document text cleaning completed",
            document_id=document_id,
            pages=len(cleaned_document.pages),
            raw_characters=len(raw_text),
            cleaned_characters=len(cleaned_text),
            cleaned_preview=cleaned_text[:500],
        )

        return cleaned_document

    except DocumentExtractionError as exc:
        logger.error(
            "PDF extraction failed",
            document_id=document_id,
            error=str(exc),
        )
        raise

    except Exception as exc:
        logger.exception(
            "Document processing failed",
            document_id=document_id,
            error=str(exc),
        )
        raise
