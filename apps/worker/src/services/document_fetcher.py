from dataclasses import dataclass

import structlog
from sqlalchemy import select

from src.db import AsyncSessionLocal
from src.models.document import Document
from src.storage.object_storage import ObjectStorage, get_object_storage

logger = structlog.get_logger()


@dataclass(frozen=True)
class FetchedDocument:
    """Document metadata and original PDF content required for processing."""

    id: str
    storage_key: str
    status: str
    content: bytes


class DocumentFetcher:
    """
    Loads document metadata from PostgreSQL and downloads
    the original PDF from object storage.
    """

    def __init__(self, storage: ObjectStorage | None = None) -> None:
        self.storage = storage or get_object_storage()

    async def fetch(self, document_id: str) -> FetchedDocument:
        """
        Fetch a document using its ID.

        Steps:
        1. Load document metadata from PostgreSQL.
        2. Read its storage key.
        3. Download the original PDF.
        4. Return metadata together with PDF bytes.
        """

        document = await self._get_document(document_id)

        logger.info(
            "Document metadata loaded",
            document_id=document.id,
            storage_key=document.storage_key,
            status=document.status,
        )

        content = await self.storage.download(document.storage_key)

        if not content:
            raise RuntimeError(f"Downloaded document is empty: {document_id}")

        logger.info(
            "Document downloaded for processing",
            document_id=document.id,
            size=len(content),
        )

        return FetchedDocument(
            id=document.id,
            storage_key=document.storage_key,
            status=document.status,
            content=content,
        )

    async def _get_document(self, document_id: str) -> Document:
        """Load a document from PostgreSQL by its ID."""

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )

            document = result.scalar_one_or_none()

        if document is None:
            logger.error(
                "Document not found",
                document_id=document_id,
            )

            raise ValueError(f"Document not found: {document_id}")

        return document
