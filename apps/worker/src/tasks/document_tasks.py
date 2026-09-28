import asyncio

import dramatiq
import structlog

from src.processing.document_extractor import (
    DocumentExtractionError,
    DocumentExtractor,
)
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

    Current pipeline stage:
    1. Load document metadata from PostgreSQL.
    2. Download the original PDF from object storage.
    3. Extract native text from the PDF.

    Cleaning, OCR, chunking, embeddings, and vector storage
    are intentionally handled in later pipeline stages.
    """

    asyncio.run(_process_document(document_id))


async def _process_document(document_id: str) -> None:
    logger.info(
        "Document processing started",
        document_id=document_id,
    )

    fetcher = DocumentFetcher()
    extractor = DocumentExtractor()

    try:
        fetched_document = await fetcher.fetch(document_id)

        logger.info(
            "Document fetched successfully",
            document_id=document_id,
            storage_key=fetched_document.storage_key,
            size=len(fetched_document.content),
        )

        extracted_document = extractor.extract(
            fetched_document.content
        )

        logger.info(
            "Native PDF text extraction completed",
            document_id=document_id,
            extracted_characters=len(extracted_document.combined_text()),
        )

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
