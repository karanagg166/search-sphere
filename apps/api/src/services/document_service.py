import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.document import Document
from src.models.user import User
from src.repositories.document_repository import DocumentRepository
from src.repositories.ingestion_queue import ingestion_queue
from src.services.ingestion_progress import attach_job_progress
from src.storage.object_storage import ObjectStorage

logger = structlog.get_logger()

# Magic bytes identifying standard PDF documents
PDF_MAGIC_BYTES = b"%PDF-"


class DocumentService:
    """Application service coordinating document validation, storage, metadata persistence, and queueing."""

    def __init__(
        self,
        db: AsyncSession,
        storage: ObjectStorage,
        repository: DocumentRepository | None = None,
        vector_store: Any | None = None,
    ):
        self.db = db
        self.storage = storage
        self.repository = repository or DocumentRepository(db)
        self.vector_store = vector_store

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
                status="queued",
                commit=False,
            )
            await ingestion_queue.enqueue(self.db, "standalone", document_id)
            await self.db.commit()
            await self.db.refresh(document)
        except Exception as exc:
            await self.db.rollback()
            logger.error("Document registration failed", document_id=document_id, error_type=type(exc).__name__)
            # Cleanup stored file from object storage to prevent orphans
            await self.storage.delete(storage_key)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save document metadata in database.",
            ) from exc

        from src.services.ingestion_worker import ingestion_worker

        ingestion_worker.notify_new_job()

        logger.info(
            "Document uploaded successfully",
            document_id=document.id,
            user_id=user.id,
            filename=document.filename,
            file_size=document.file_size,
        )
        await attach_job_progress(self.db, "standalone", [document])
        return document

    async def list_user_documents(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Document], int]:
        """Lists documents owned by the user and total count."""
        documents = await self.repository.list_by_user(user_id, limit, offset)
        total = await self.repository.count_by_user(user_id)
        await attach_job_progress(self.db, "standalone", documents)
        return documents, total

    async def get_user_document(self, document_id: str, user_id: str) -> Document:
        """Retrieves single document metadata verifying user ownership and lazy chunk_count backfill."""
        document = await self.repository.get_by_id(document_id, user_id)
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )
        if document.status == "indexed" and getattr(document, "chunk_count", None) is None:
            try:
                v_store = self.vector_store
                if v_store is None:
                    from src.vector_store.qdrant_store import QdrantVectorStore
                    v_store = QdrantVectorStore()
                pts = await v_store.count_document_points(document.id)
                if pts > 0:
                    document.chunk_count = pts
                    await self.repository.update(document, chunk_count=pts)
            except Exception:
                pass
        await attach_job_progress(self.db, "standalone", [document])
        return document

    async def reindex_user_document(self, document_id, user_id):
        document = await self.get_user_document(document_id, user_id)
        job = await ingestion_queue.enqueue(self.db, "standalone", document.id, reset=True)
        await ingestion_queue.mirror_document_state(self.db, job)
        await self.db.commit()
        await attach_job_progress(self.db, "standalone", [document])
        from src.services.ingestion_worker import ingestion_worker

        ingestion_worker.notify_new_job()
        return document

    async def queue_pending_user_documents(self, user_id, limit=20):
        from src.services.ingestion_recovery import legacy_lease_active

        documents = await self.repository.list_by_user(user_id, limit=limit)
        queued = []
        for document in documents:
            if document.status == "indexed" or document.status.startswith("failed") or legacy_lease_active(document):
                continue
            job = await ingestion_queue.enqueue(self.db, "standalone", document.id)
            await ingestion_queue.mirror_document_state(self.db, job)
            queued.append(document.id)
        await self.db.commit()
        from src.services.ingestion_worker import ingestion_worker

        ingestion_worker.notify_new_job()
        return queued

    async def delete_user_document(self, document_id: str, user_id: str) -> None:
        """Deletes document points from Qdrant vector store, file from Object Storage, and PostgreSQL metadata."""
        document = await self.repository.get_by_id(document_id, user_id)
        if not document:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Document not found.",
            )

        # 1. Purge points from Qdrant vector store
        v_store = self.vector_store
        if v_store is None:
            try:
                from src.vector_store import get_vector_store
                v_store = get_vector_store()
            except Exception as exc:
                logger.warning("Could not initialize vector store for point deletion", error=str(exc))
                v_store = None

        if v_store is not None:
            try:
                await v_store.delete_document_points(document_id=document.id)
                logger.info("Deleted Qdrant vector points for document", document_id=document_id, user_id=user_id)
            except Exception as exc:
                logger.warning(
                    "Failed to delete Qdrant points during document deletion (proceeding with storage and DB cleanup)",
                    document_id=document_id,
                    error=str(exc),
                )

        # 2. Remove from Object Storage
        try:
            await self.storage.delete(document.storage_key)
        except Exception as exc:
            logger.warning(
                "Failed to delete file from object storage (proceeding with DB cleanup)",
                storage_key=document.storage_key,
                error=str(exc),
            )

        # 3. Remove from Database
        await self.repository.delete(document)
        logger.info("Document deleted successfully", document_id=document_id, user_id=user_id)
