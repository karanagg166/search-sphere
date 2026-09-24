import uuid
from pathlib import Path

import structlog
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.document import Document
from src.models.user import User
from src.repositories.document_repository import DocumentRepository
from src.storage.object_storage import ObjectStorage
from src.tasks.document_tasks import enqueue_document

logger = structlog.get_logger()

# Magic bytes identifying standard PDF documents
PDF_MAGIC_BYTES = b"%PDF-"


class DocumentService:
    """Application service coordinating document validation, storage, metadata persistence, and queueing."""

    def __init__(self, db: AsyncSession, storage: ObjectStorage):
        self.db = db
        self.storage = storage
        self.repository = DocumentRepository(db)

    def validate_file_content(self, content: bytes, filename: str) -> None:
        """Strictly validates file size and binary header signature."""
        if not content or len(content) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
            max_mb = settings.MAX_UPLOAD_SIZE_BYTES // (1024 * 1024)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the maximum allowed size of {max_mb} MB.",
            )

        # Verify PDF magic signature at the start of the file
        if not content.startswith(PDF_MAGIC_BYTES):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file format. File must be a valid PDF document with a standard %PDF- header.",
            )

    async def upload_document(self, file: UploadFile, user: User) -> Document:
        """Validates, stores raw PDF in object storage, records metadata in PostgreSQL, and enqueues task."""
        raw_filename = file.filename or "document.pdf"
        sanitized_filename = Path(raw_filename).name

        try:
            content = await file.read()
        except Exception as exc:
            logger.error("Failed to read uploaded file", error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to read uploaded file contents.",
            ) from exc

        # 1. Strict Validation
        self.validate_file_content(content, sanitized_filename)

        # 2. Generate unique document identity & storage key
        document_id = str(uuid.uuid4())
        storage_key = f"documents/{user.id}/{document_id}.pdf"
        file_size = len(content)

        # 3. Store raw PDF in Object Storage
        try:
            file_url = await self.storage.upload(
                key=storage_key,
                data=content,
                content_type="application/pdf",
            )
        except Exception as exc:
            logger.error("Storage upload failed", key=storage_key, error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to persist file into object storage.",
            ) from exc

        # 4. Store document metadata in PostgreSQL
        try:
            document = await self.repository.create(
                document_id=document_id,
                user_id=user.id,
                filename=sanitized_filename,
                storage_key=storage_key,
                file_url=file_url,
                file_size=file_size,
                mime_type="application/pdf",
                status="uploaded",
            )
        except Exception as exc:
            logger.error(
                "Database persistence failed", document_id=document_id, error=str(exc)
            )
            # Cleanup stored file from object storage to prevent orphans
            await self.storage.delete(storage_key)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save document metadata in database.",
            ) from exc

        # 5. Push document to worker queue for background processing (next phase)
        enqueue_document(document_id)

        logger.info(
            "Document uploaded successfully",
            document_id=document.id,
            user_id=user.id,
            filename=document.filename,
            file_size=document.file_size,
        )
        return document

    async def list_user_documents(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Document], int]:
        """Lists documents owned by the user and total count."""
        documents = await self.repository.list_by_user(user_id, limit, offset)
        total = await self.repository.count_by_user(user_id)
        return documents, total

    async def get_user_document(self, document_id: str, user_id: str) -> Document:
        """Retrieves single document metadata verifying user ownership."""
        document = await self.repository.get_by_id(document_id, user_id)
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )
        return document

    async def delete_user_document(self, document_id: str, user_id: str) -> None:
        """Deletes document from both Object Storage and PostgreSQL."""
        document = await self.repository.get_by_id(document_id, user_id)
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )

        # Remove from Object Storage
        await self.storage.delete(document.storage_key)

        # Remove from Database
        await self.repository.delete(document)
        logger.info("Document deleted", document_id=document_id, user_id=user_id)
