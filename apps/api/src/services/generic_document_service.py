import os
import re
import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.collection import DocumentCollection
from src.models.external_document import ExternalDocument
from src.schemas.v1.documents import (
    DocumentListResponse,
    DocumentRegisterRequest,
    DocumentResponse,
)
from src.security.service_context import ServiceContext
from src.storage.object_storage import ObjectStorage
from src.tasks.document_tasks import enqueue_generic_document
from src.vector_store.qdrant_store import QdrantVectorStore

logger = structlog.get_logger()

MAX_GENERIC_DOC_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_GENERIC_MIME_TYPES = {
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
    "text/plain",
    "text/markdown",
    "text/csv",
}


def sanitize_path_segment(value: str, field_name: str) -> str:
    """Validates and cleans path segment to prevent directory traversal."""
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{field_name} is required and cannot be empty.",
        )
    if cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="Path traversal is not permitted.")
    if not re.match(r"^[a-zA-Z0-9_\-\.:]+$", cleaned):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid characters in {field_name}. Only alphanumeric, hyphens, dots, and underscores allowed.",
        )
    return cleaned


def sanitize_filename(filename: str) -> str:
    """Sanitizes file name to prevent directory traversal and illegal characters."""
    base_name = os.path.basename(filename).strip()
    if not base_name:
        base_name = "document"

    name_part, ext = os.path.splitext(base_name)
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name_part).strip("._")
    if not clean_name:
        clean_name = "document"

    clean_ext = re.sub(r"[^a-zA-Z0-9]", "", ext).lower()
    if clean_ext:
        return f"{clean_name[:100]}.{clean_ext[:10]}"
    return clean_name[:100]


def detect_and_validate_file_type(data: bytes, reported_content_type: str | None) -> str:
    """Validates magic bytes or text encoding against allowed MIME types."""
    detected: str | None = None

    if data.startswith(b"%PDF"):
        detected = "application/pdf"
    elif data.startswith(b"\xff\xd8\xff"):
        detected = "image/jpeg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        detected = "image/webp"
    else:
        # Check if UTF-8 plain text/markdown
        try:
            data.decode("utf-8")
            if reported_content_type in ("text/markdown", "text/x-markdown"):
                detected = "text/markdown"
            elif reported_content_type == "text/csv":
                detected = "text/csv"
            else:
                detected = "text/plain"
        except UnicodeDecodeError:
            detected = None

    if not detected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Supported formats: PDF, JPEG, PNG, WebP, Plain Text, Markdown.",
        )

    if reported_content_type == "image/jpg":
        reported_content_type = "image/jpeg"

    if detected in ("application/pdf", "image/jpeg", "image/png", "image/webp"):
        from src.processing.file_validation import validate_binary_document
        validate_binary_document(data, detected, reported_content_type)
    elif reported_content_type not in (None, "", detected, "text/x-markdown"):
        raise HTTPException(status_code=400, detail="Reported MIME type does not match file content.")
    return detected


class GenericDocumentService:
    def __init__(self, db: AsyncSession, storage: ObjectStorage, delete_extension=None):
        self.db = db
        self.storage = storage
        self.delete_extension = delete_extension

    async def upload_file(
        self,
        context: ServiceContext,
        file_bytes: bytes,
        original_filename: str,
        content_type: str | None,
        document_id: str,
        collection_id: str | None = None,
    ) -> tuple[str, str, int]:
        """Validates and uploads raw file bytes to isolated multi-tenant object storage path."""
        if len(file_bytes) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file is empty.",
            )

        if len(file_bytes) > MAX_GENERIC_DOC_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"File exceeds maximum allowed size of {MAX_GENERIC_DOC_SIZE_BYTES // (1024 * 1024)}MB.",
            )

        context = context.resolve_scope(collection_id)
        collection_id = context.collection_id
        existing = (await self.db.execute(select(ExternalDocument).where(
            ExternalDocument.source_system == context.client_id,
            ExternalDocument.tenant_id == context.tenant_id,
            ExternalDocument.external_document_id == document_id,
        ))).scalar_one_or_none()
        if existing and (existing.owner_subject_id != context.subject_id or existing.collection_id != collection_id):
            raise HTTPException(status_code=403, detail="Document belongs to another scope.")
        detected_mime = detect_and_validate_file_type(file_bytes, content_type)
        clean_doc_id = sanitize_path_segment(document_id, "document_id")
        clean_filename = sanitize_filename(original_filename)
        clean_client_id = sanitize_path_segment(context.client_id, "client_id")
        clean_tenant_id = sanitize_path_segment(context.tenant_id, "tenant_id")
        coll_segment = sanitize_path_segment(collection_id, "collection_id") if collection_id else "default"

        storage_path = f"documents/{clean_client_id}/{clean_tenant_id}/{coll_segment}/{clean_doc_id}/{clean_filename}"

        logger.info(
            "Uploading document to object storage",
            client_id=context.client_id,
            tenant_id=context.tenant_id,
            document_id=clean_doc_id,
            storage_path=storage_path,
            mime=detected_mime,
            size=len(file_bytes),
        )

        try:
            await self.storage.upload(key=storage_path, data=file_bytes, content_type=detected_mime)
        except Exception as exc:
            logger.error("Failed to upload document", storage_path=storage_path, error=str(exc))
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to store document in object storage.",
            ) from exc

        return storage_path, detected_mime, len(file_bytes)

    async def register_document(
        self,
        context: ServiceContext,
        request: DocumentRegisterRequest,
        request_id: str | None = None,
    ) -> ExternalDocument:
        """Registers or updates a multi-tenant document and enqueues it for asynchronous processing."""
        clean_ext_id = sanitize_path_segment(request.external_document_id, "external_document_id")
        context = context.resolve_scope(request.collection_id, request.owner_subject_id)
        storage_key = request.storage_key.strip()
        expected_prefix = f"documents/{context.client_id}/{context.tenant_id}/{context.collection_id or 'default'}/{clean_ext_id}/"

        if ".." in storage_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Path traversal is not permitted in storage key.",
            )

        if not storage_key.startswith(expected_prefix):
            raise HTTPException(status_code=403, detail="Storage object is outside the requested document scope.")

        # Validate collection existence if provided
        if request.collection_id:
            coll_stmt = select(DocumentCollection).where(
                DocumentCollection.client_id == context.client_id,
                DocumentCollection.tenant_id == context.tenant_id,
                DocumentCollection.collection_id == request.collection_id,
            )
            coll_res = await self.db.execute(coll_stmt)
            if not coll_res.scalar_one_or_none():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Collection '{request.collection_id}' does not exist in this tenant scope.",
                )

        clean_file_name = sanitize_filename(request.file_name)
        clean_doc_type = request.document_type.strip().upper()

        # Check existing record for this client + tenant + external_document_id
        stmt = select(ExternalDocument).where(
            ExternalDocument.source_system == context.client_id,
            ExternalDocument.tenant_id == context.tenant_id,
            ExternalDocument.external_document_id == clean_ext_id,
        )
        res = await self.db.execute(stmt)
        doc = res.scalar_one_or_none()

        if doc:
            if doc.owner_subject_id != context.subject_id or doc.collection_id != context.collection_id:
                raise HTTPException(status_code=409, detail="Document identity is already assigned to another scope.")
            doc.collection_id = context.collection_id
            doc.owner_subject_id = context.subject_id
            doc.storage_path = storage_key
            doc.file_name = clean_file_name
            doc.mime_type = request.mime_type
            doc.file_size = request.file_size
            doc.document_type = clean_doc_type
            doc.metadata_json = request.metadata
            doc.external_patient_id = context.subject_id or "default"
            doc.status = "QUEUED"
            doc.processing_error = None
        else:
            doc = ExternalDocument(
                source_system=context.client_id,
                tenant_id=context.tenant_id,
                collection_id=context.collection_id,
                owner_subject_id=context.subject_id,
                external_patient_id=context.subject_id or "default",
                external_document_id=clean_ext_id,
                storage_path=storage_key,
                file_name=clean_file_name,
                mime_type=request.mime_type,
                file_size=request.file_size,
                document_type=clean_doc_type,
                metadata_json=request.metadata,
                status="QUEUED",
            )
            self.db.add(doc)

        await self.db.commit()
        await self.db.refresh(doc)

        # Enqueue processing task using the database primary key ID
        if not enqueue_generic_document(doc.id, request_id=request_id):
            doc.status = "FAILED"
            doc.processing_error = "Processing queue is unavailable; retry registration."
            await self.db.commit()
            await self.db.refresh(doc)

        logger.info(
            "Registered document and enqueued processing",
            doc_id=doc.id,
            client_id=context.client_id,
            tenant_id=context.tenant_id,
            external_document_id=clean_ext_id,
        )
        return doc

    async def get_document(
        self,
        context: ServiceContext,
        doc_id_or_external_id: str,
    ) -> ExternalDocument:
        """Finds document strictly scoped to authenticated client and tenant."""
        stmt = select(ExternalDocument).where(
            ExternalDocument.source_system == context.client_id,
            ExternalDocument.tenant_id == context.tenant_id,
            (
                (ExternalDocument.id == doc_id_or_external_id)
                | (ExternalDocument.external_document_id == doc_id_or_external_id)
            ),
        )
        if context.subject_id:
            stmt = stmt.where(ExternalDocument.owner_subject_id == context.subject_id)
        if context.collection_id:
            stmt = stmt.where(ExternalDocument.collection_id == context.collection_id)
        res = await self.db.execute(stmt)
        doc = res.scalar_one_or_none()
        if not doc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document '{doc_id_or_external_id}' not found.",
            )
        return doc

    async def list_documents(
        self,
        context: ServiceContext,
        collection_id: str | None = None,
        owner_subject_id: str | None = None,
        status_filter: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ExternalDocument], int]:
        """Lists documents strictly scoped to client and tenant with optional filters."""
        context = context.resolve_scope(collection_id, owner_subject_id)
        collection_id, owner_subject_id = context.collection_id, context.subject_id
        base_stmt = select(ExternalDocument).where(
            ExternalDocument.source_system == context.client_id,
            ExternalDocument.tenant_id == context.tenant_id,
        )
        if collection_id:
            base_stmt = base_stmt.where(ExternalDocument.collection_id == collection_id)
        if owner_subject_id:
            base_stmt = base_stmt.where(ExternalDocument.owner_subject_id == owner_subject_id)
        if status_filter:
            base_stmt = base_stmt.where(ExternalDocument.status == status_filter.upper())

        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        total = (await self.db.execute(count_stmt)).scalar() or 0

        stmt = base_stmt.order_by(ExternalDocument.created_at.desc()).limit(limit).offset(offset)
        res = await self.db.execute(stmt)
        return list(res.scalars().all()), total

    async def delete_document(
        self,
        context: ServiceContext,
        doc_id_or_external_id: str,
        vector_store: QdrantVectorStore | None = None,
    ) -> bool:
        """Deletes document from database, object storage, and vector store with strict tenant isolation."""
        doc = await self.get_document(context, doc_id_or_external_id)

        # External IDs can collide across clients; every mutation uses the scope.
        if vector_store:
            try:
                await vector_store.delete_document_points(doc.external_document_id, filters={
                    "client_id": doc.client_id, "tenant_id": doc.tenant_id,
                })
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Document deletion is temporarily unavailable.") from exc
        try:
            await self.storage.delete(key=doc.storage_path)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Document deletion is temporarily unavailable.") from exc

        if self.delete_extension is not None:
            await self.delete_extension(self.db, doc)

        # 3. Delete database record
        await self.db.delete(doc)
        await self.db.commit()

        logger.info(
            "Document deleted successfully",
            doc_id=doc.id,
            client_id=context.client_id,
            tenant_id=context.tenant_id,
        )
        return True

    async def get_signed_url(
        self,
        context: ServiceContext,
        doc_id_or_external_id: str,
        expires_in: int = 600,
    ) -> tuple[str, int]:
        """Generates signed URL for authorized document access."""
        doc = await self.get_document(context, doc_id_or_external_id)
        expires_in = max(300, min(expires_in, 3600))
        try:
            signed_url = await self.storage.create_signed_url(key=doc.storage_path, expires_in=expires_in)
            return signed_url, expires_in
        except Exception as exc:
            logger.error(
                "Failed to generate signed URL",
                storage_path=doc.storage_path,
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Failed to generate secure access URL.",
            ) from exc
