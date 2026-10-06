import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import dramatiq
import structlog
from sqlalchemy import delete, select

from src.db import AsyncSessionLocal
from src.models.external_document import ExternalDocument
from src.models.external_document_content import ExternalDocumentContent
from src.models.medical_observation import MedicalObservation
from src.processing.chunking import DocumentChunker
from src.processing.cleaning import TextCleaner
from src.processing.embedding import (
    DenseEmbedder,
    DenseEmbeddingError,
)
from src.processing.extraction import (
    DocumentExtractionError,
    DocumentExtractor,
    MedicalObservationExtractor,
)
from src.processing.models.document import (
    ChunkedDocument,
    CleanedDocument,
    EmbeddedDocument,
    ExtractedDocument,
)
from src.processing.sparse_embedding import (
    BM25Embedder,
    SparseEmbeddingError,
)
from src.services.document_fetcher import DocumentFetcher
from src.storage.object_storage import ObjectStorage, get_object_storage
from src.vector_store import QdrantVectorStore, QdrantVectorStoreError

logger = structlog.get_logger()


@dramatiq.actor(
    queue_name="default",
    actor_name="process_document_task",
    max_retries=3,
)
def process_document_task(document_id: str) -> None:
    """Background job responsible for processing a Search Sphere document."""
    asyncio.run(_process_document(document_id))


@dramatiq.actor(
    queue_name="default",
    actor_name="process_medical_document_task",
    max_retries=3,
)
def process_medical_document_task(document_id: str, request_id: str | None = None) -> None:
    """Background job responsible for processing a Quick Clinic medical document."""
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    asyncio.run(_process_medical_document(document_id, request_id=request_id))


@dramatiq.actor(
    queue_name="default",
    actor_name="process_generic_document_task",
    max_retries=3,
)
def process_generic_document_task(document_id: str, request_id: str | None = None) -> None:
    """Background job responsible for processing a generic multi-tenant external document."""
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    asyncio.run(_process_generic_document(document_id, request_id=request_id))


async def _process_pipeline(
    document_id: str,
    raw_content: bytes,
    mime_type: str,
    extractor: DocumentExtractor,
    cleaner: TextCleaner,
    chunker: DocumentChunker,
    embedder: DenseEmbedder,
    sparse_embedder: BM25Embedder,
    vector_store: QdrantVectorStore,
    extra_payload: dict[str, Any] | None = None,
    is_medical: bool = False,
    patient_id: str | None = None,
) -> tuple[ExtractedDocument, CleanedDocument, ChunkedDocument, EmbeddedDocument | None, int]:
    """
    Shared ingestion pipeline executed across document sources.
    Handles extraction, cleaning, chunking, dense/sparse embeddings, and vector indexing.
    Respects PHI logging safety when is_medical=True.
    """
    if is_medical:
        extracted_doc = extractor.extract(raw_content, mime_type=mime_type)
    else:
        extracted_doc = extractor.extract(raw_content)

    raw_text = extracted_doc.combined_text()

    if is_medical:
        logger.info(
            "Medical document extraction completed",
            document_id=document_id,
            patient_id=patient_id,
            pages=len(extracted_doc.pages),
            extracted_characters=len(raw_text),
        )
    else:
        logger.info(
            "Document extraction completed",
            document_id=document_id,
            pages=len(extracted_doc.pages),
            extracted_characters=len(raw_text),
            preview=raw_text[:500],
        )

    cleaned_doc = cleaner.clean_document(extracted_doc)
    cleaned_text = cleaned_doc.combined_text()

    if is_medical:
        logger.info(
            "Medical document text cleaning completed",
            document_id=document_id,
            patient_id=patient_id,
            pages=len(cleaned_doc.pages),
            raw_characters=len(raw_text),
            cleaned_characters=len(cleaned_text),
        )
    else:
        logger.info(
            "Document text cleaning completed",
            document_id=document_id,
            pages=len(cleaned_doc.pages),
            raw_characters=len(raw_text),
            cleaned_characters=len(cleaned_text),
            cleaned_preview=cleaned_text[:500],
        )

    chunked_doc = chunker.chunk_document(cleaned_doc)
    total_chunks = len(chunked_doc.chunks)

    if is_medical:
        logger.info(
            "Medical document chunking completed",
            document_id=document_id,
            patient_id=patient_id,
            pages=len(cleaned_doc.pages),
            chunks=total_chunks,
            total_tokens=chunked_doc.total_tokens(),
        )
    else:
        first_chunk_preview = chunked_doc.chunks[0].content[:200] if total_chunks > 0 else ""
        logger.info(
            "Document chunking completed",
            document_id=document_id,
            pages=len(cleaned_doc.pages),
            chunks=total_chunks,
            total_tokens=chunked_doc.total_tokens(),
            first_chunk_preview=first_chunk_preview,
        )

    if is_medical and total_chunks == 0:
        await vector_store.delete_document_points(document_id)
        return extracted_doc, cleaned_doc, chunked_doc, None, 0

    embedded_doc = embedder.embed_document(chunked_doc)

    if not is_medical:
        logger.info(
            "Document embedding completed",
            document_id=document_id,
            chunks=embedded_doc.total_chunks(),
            total_embedded_chunks=embedded_doc.total_chunks(),
            embedding_model=getattr(embedder, "model_name", "dense"),
            embedding_dimension=getattr(embedder, "dimension", 384),
            batch_size=getattr(embedder, "batch_size", 32),
            first_chunk_preview=(
                embedded_doc.chunks[0].content[:200]
                if embedded_doc.total_chunks() > 0
                else ""
            ),
        )

    sparse_vectors = sparse_embedder.embed_chunks(chunked_doc.chunks)

    if not is_medical:
        logger.info(
            "Document sparse BM25 embedding completed",
            document_id=document_id,
            chunks=len(sparse_vectors),
            sparse_model=getattr(sparse_embedder, "model_name", "bm25"),
        )

    if extra_payload is not None:
        points_written = await vector_store.index_document(
            document_id,
            embedded_doc,
            sparse_vectors=sparse_vectors,
            extra_payload=extra_payload,
        )
    else:
        points_written = await vector_store.index_document(
            document_id,
            embedded_doc,
            sparse_vectors=sparse_vectors,
        )

    if is_medical:
        logger.info(
            "Medical document vector indexing completed",
            document_id=document_id,
            patient_id=patient_id,
            chunks=total_chunks,
            points_written=points_written,
        )
    else:
        logger.info(
            "Document vector indexing completed",
            document_id=document_id,
            collection=vector_store.collection_name,
            points_written=points_written,
        )

    return extracted_doc, cleaned_doc, chunked_doc, embedded_doc, points_written


async def _process_document(
    document_id: str,
    fetcher: DocumentFetcher | None = None,
    extractor: DocumentExtractor | None = None,
    cleaner: TextCleaner | None = None,
    chunker: DocumentChunker | None = None,
    embedder: DenseEmbedder | None = None,
    sparse_embedder: BM25Embedder | None = None,
    vector_store: QdrantVectorStore | None = None,
) -> EmbeddedDocument:
    """Processes Search Sphere internal documents."""
    logger.info("Document processing started", document_id=document_id)

    doc_fetcher = fetcher or DocumentFetcher()
    doc_extractor = extractor or DocumentExtractor()
    doc_cleaner = cleaner or TextCleaner()
    doc_chunker = chunker or DocumentChunker()
    doc_embedder = embedder or DenseEmbedder()
    doc_sparse_embedder = sparse_embedder or BM25Embedder()
    doc_vector_store = vector_store or QdrantVectorStore(sparse_embedder=doc_sparse_embedder)

    try:
        fetched_document = await doc_fetcher.fetch(document_id)
        _, _, _, embedded_doc, _ = await _process_pipeline(
            document_id=document_id,
            raw_content=fetched_document.content,
            mime_type="application/pdf",
            extractor=doc_extractor,
            cleaner=doc_cleaner,
            chunker=doc_chunker,
            embedder=doc_embedder,
            sparse_embedder=doc_sparse_embedder,
            vector_store=doc_vector_store,
            is_medical=False,
        )
        return embedded_doc or EmbeddedDocument(chunks=[])
    except Exception as exc:
        logger.exception("Document processing failed", document_id=document_id, error=str(exc))
        raise
    finally:
        if vector_store is None:
            await doc_vector_store.close()


async def _process_generic_document(
    document_id: str,
    request_id: str | None = None,
    storage: ObjectStorage | None = None,
    extractor: DocumentExtractor | None = None,
    cleaner: TextCleaner | None = None,
    chunker: DocumentChunker | None = None,
    embedder: DenseEmbedder | None = None,
    sparse_embedder: BM25Embedder | None = None,
    vector_store: QdrantVectorStore | None = None,
    session_factory: Any | None = None,
    force_medical: bool = False,
) -> EmbeddedDocument | None:
    """Processes external documents across arbitrary clients and tenants with isolation."""
    start_time = time.time()
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    get_session = session_factory or AsyncSessionLocal

    async with get_session() as session:
        result = await session.execute(
            select(ExternalDocument).where(
                (ExternalDocument.id == document_id) | (ExternalDocument.external_document_id == document_id)
            )
        )
        external_doc = result.scalar_one_or_none()
        if not external_doc:
            raise ValueError(f"External document not found: {document_id}")

        external_doc.status = "PROCESSING"
        external_doc.processing_error = None
        await session.commit()

        storage_path = external_doc.storage_path
        mime_type = external_doc.mime_type
        client_id = external_doc.client_id
        tenant_id = external_doc.tenant_id
        collection_id = external_doc.collection_id
        owner_subject_id = external_doc.owner_subject_id or external_doc.external_patient_id
        doc_type = external_doc.document_type
        file_name = external_doc.file_name
        report_date_iso = external_doc.report_date.isoformat() if external_doc.report_date else None
        db_doc_id = external_doc.id
        source_doc_id = external_doc.external_document_id

    is_medical = force_medical or (client_id == "quick_clinic") or (external_doc.source_system == "quick_clinic")

    if is_medical:
        logger.info(
            "Medical document processing started",
            document_id=source_doc_id,
            patient_id=owner_subject_id,
            mime_type=mime_type,
        )
    else:
        logger.info(
            "Document processing started",
            document_id=source_doc_id,
            client_id=client_id,
            tenant_id=tenant_id,
            mime_type=mime_type,
        )

    doc_storage = storage or get_object_storage()
    doc_extractor = extractor or DocumentExtractor()
    doc_cleaner = cleaner or TextCleaner()
    doc_chunker = chunker or DocumentChunker()
    doc_embedder = embedder or DenseEmbedder()
    doc_sparse_embedder = sparse_embedder or BM25Embedder()
    doc_vector_store = vector_store or QdrantVectorStore(sparse_embedder=doc_sparse_embedder)

    try:
        content = await doc_storage.download(storage_path)
        if not content:
            raise RuntimeError(f"Downloaded document is empty: {document_id}")

        extra_payload = {
            "client_id": client_id,
            "tenant_id": tenant_id,
            "collection_id": collection_id,
            "owner_subject_id": owner_subject_id,
            "document_id": source_doc_id,
            "document_type": doc_type,
            "report_date": report_date_iso,
            "file_name": file_name,
            # Backward-compatibility payload fields
            "source_system": external_doc.source_system,
            "patient_id": external_doc.external_patient_id,
        }

        extracted_doc, _, chunked_doc, embedded_doc, points_written = await _process_pipeline(
            document_id=source_doc_id,
            raw_content=content,
            mime_type=mime_type,
            extractor=doc_extractor,
            cleaner=doc_cleaner,
            chunker=doc_chunker,
            embedder=doc_embedder,
            sparse_embedder=doc_sparse_embedder,
            vector_store=doc_vector_store,
            extra_payload=extra_payload,
            is_medical=is_medical,
            patient_id=owner_subject_id if is_medical else None,
        )

        extracted_observations = []
        if is_medical:
            is_ocr = mime_type.lower() in ("image/jpeg", "image/png", "image/webp", "image/jpg")
            default_method = "OCR" if is_ocr else "REGEX"
            observation_extractor = MedicalObservationExtractor()
            extracted_observations = observation_extractor.extract_from_extracted_document(
                extracted_doc=extracted_doc,
                report_date=external_doc.report_date,
                default_extraction_method=default_method,
            )

        async with get_session() as session:
            raw_text = extracted_doc.combined_text()
            char_count = len(raw_text)

            res_content = await session.execute(
                select(ExternalDocumentContent).where(
                    ExternalDocumentContent.external_document_id == db_doc_id
                )
            )
            content_row = res_content.scalar_one_or_none()
            if content_row:
                content_row.raw_text = raw_text
                content_row.character_count = char_count
            else:
                content_row = ExternalDocumentContent(
                    external_document_id=db_doc_id,
                    raw_text=raw_text,
                    character_count=char_count,
                )
                session.add(content_row)

            if is_medical:
                await session.execute(
                    delete(MedicalObservation).where(
                        MedicalObservation.source_system == external_doc.source_system,
                        MedicalObservation.external_document_id == source_doc_id,
                    )
                )

                for obs in extracted_observations:
                    obs_row = MedicalObservation(
                        source_system=external_doc.source_system,
                        external_patient_id=owner_subject_id,
                        external_document_id=source_doc_id,
                        observation_type=obs.observation_type,
                        display_name=obs.display_name,
                        value_numeric=obs.value_numeric,
                        value_text=obs.value_text,
                        value_secondary_numeric=obs.value_secondary_numeric,
                        unit=obs.unit,
                        observed_at=obs.observed_at,
                        reported_at=obs.reported_at,
                        is_date_inferred=obs.is_date_inferred,
                        page_number=obs.page_number,
                        chunk_index=obs.chunk_index,
                        confidence=obs.confidence,
                        extraction_method=obs.extraction_method,
                    )
                    session.add(obs_row)

            res_doc = await session.execute(
                select(ExternalDocument).where(ExternalDocument.id == db_doc_id)
            )
            doc_in_session = res_doc.scalar_one()
            doc_in_session.status = "READY"
            doc_in_session.processed_at = datetime.now(timezone.utc)
            doc_in_session.processing_error = None
            await session.commit()

        duration = round(time.time() - start_time, 2)
        logger.info(
            "Document processing succeeded",
            document_id=source_doc_id,
            client_id=client_id,
            tenant_id=tenant_id,
            pages=len(extracted_doc.pages),
            characters=len(raw_text),
            chunk_count=len(chunked_doc.chunks),
            is_medical=is_medical,
            processing_status="READY",
            duration=duration,
        )
        return embedded_doc

    except Exception as exc:
        duration = round(time.time() - start_time, 2)
        logger.error(
            "Document processing failed",
            document_id=document_id,
            processing_status="FAILED",
            duration=duration,
            error=str(exc),
        )

        try:
            async with get_session() as session:
                res_doc = await session.execute(
                    select(ExternalDocument).where(ExternalDocument.id == db_doc_id)
                )
                doc_in_session = res_doc.scalar_one_or_none()
                if doc_in_session:
                    doc_in_session.status = "FAILED"
                    doc_in_session.processing_error = str(exc)
                    await session.commit()
        except Exception as db_err:
            logger.error("Failed to persist FAILED status to database", error=str(db_err))

        raise

    finally:
        if vector_store is None:
            await doc_vector_store.close()


async def _process_medical_document(
    document_id: str,
    request_id: str | None = None,
    storage: ObjectStorage | None = None,
    extractor: DocumentExtractor | None = None,
    cleaner: TextCleaner | None = None,
    chunker: DocumentChunker | None = None,
    embedder: DenseEmbedder | None = None,
    sparse_embedder: BM25Embedder | None = None,
    vector_store: QdrantVectorStore | None = None,
    session_factory: Any | None = None,
) -> EmbeddedDocument | None:
    """Processes external medical documents from Quick Clinic with strict isolation & PHI logging."""
    return await _process_generic_document(
        document_id=document_id,
        request_id=request_id,
        storage=storage,
        extractor=extractor,
        cleaner=cleaner,
        chunker=chunker,
        embedder=embedder,
        sparse_embedder=sparse_embedder,
        vector_store=vector_store,
        session_factory=session_factory,
        force_medical=True,
    )
