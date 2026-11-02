from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import or_, select

from src.db import AsyncSessionLocal
from src.models.document import Document
from src.processing.chunking import DocumentChunker
from src.processing.cleaning import TextCleaner
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import DocumentExtractor
from src.processing.sparse_embedding.bm25_embedder import BM25Embedder
from src.storage.object_storage import get_object_storage
from src.vector_store.qdrant_store import QdrantVectorStore

logger = structlog.get_logger()

# Track in-flight document indexing to prevent duplicate processing within the same process
_active_indexing_ids: set[str] = set()

MAX_INDEXING_RETRIES = 3
STALE_PROCESSING_TIMEOUT_SECONDS = 300  # 5 minutes


async def index_document_pipeline(document_id: str) -> bool:
    """
    In-process indexing pipeline for a document.

    Ensures that when background workers or RabbitMQ are unavailable
    (e.g., single-container web deployment on Render free tier), documents
    are fully extracted, cleaned, chunked, embedded, and indexed into Qdrant.
    Uses bounded retries and safe atomic state transitions.
    """
    logger.info("Starting in-process document indexing", document_id=document_id)

    # 1. Fetch document metadata and claim it with bounded retry tracking
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Document).where(Document.id == document_id)
        )
        document = result.scalar_one_or_none()
        if not document:
            logger.error("Document not found for indexing", document_id=document_id)
            return False

        if document.status == "indexed":
            logger.info("Document already indexed, skipping", document_id=document_id)
            return True

        # Parse attempt count from status (e.g. "uploaded", "uploaded:retry:2", "processing:attempt:1")
        attempt = 1
        if ":retry:" in document.status:
            try:
                attempt = int(document.status.split(":")[-1])
            except ValueError:
                attempt = 1
        elif ":attempt:" in document.status:
            try:
                attempt = int(document.status.split(":")[-1])
            except ValueError:
                attempt = 1

        if attempt > MAX_INDEXING_RETRIES:
            logger.warning("Document exceeded maximum retries", document_id=document_id, attempts=attempt)
            document.status = "failed"
            await session.commit()
            return False

        storage_key = document.storage_key
        # Mark as processing with lease attempt
        document.status = f"processing:attempt:{attempt}"
        await session.commit()

    storage = get_object_storage()
    vector_store = None

    try:
        # 2. Download raw PDF content
        pdf_bytes = await storage.download(storage_key)
        if not pdf_bytes:
            raise RuntimeError(f"Downloaded PDF content is empty for {storage_key}")

        logger.info(
            "PDF downloaded for in-process indexing",
            document_id=document_id,
            size_bytes=len(pdf_bytes),
        )

        # 3. Multimodal extraction
        extractor = DocumentExtractor()
        extracted_doc = extractor.extract(pdf_bytes)
        logger.info(
            "PDF text extraction completed",
            document_id=document_id,
            page_count=len(extracted_doc.pages),
            extracted_chars=len(extracted_doc.combined_text()),
        )

        # 4. Text cleaning
        cleaner = TextCleaner()
        cleaned_doc = cleaner.clean_document(extracted_doc)

        # 5. Semantic chunking
        chunker = DocumentChunker()
        chunked_doc = chunker.chunk_document(cleaned_doc)
        chunk_count = len(chunked_doc.chunks)
        logger.info(
            "Document chunking completed",
            document_id=document_id,
            chunk_count=chunk_count,
        )

        if chunk_count > 0:
            # 6. Dense embeddings (FastEmbed / SentenceTransformers)
            embedder = DenseEmbedder()
            embedded_doc = embedder.embed_document(chunked_doc)

            # 7. Sparse lexical BM25 embeddings
            sparse_embedder = BM25Embedder()
            sparse_vectors = sparse_embedder.embed_chunks(chunked_doc.chunks)

            # 8. Qdrant vector index synchronization
            vector_store = QdrantVectorStore(sparse_embedder=sparse_embedder)
            points_written = await vector_store.index_document(
                document_id=document_id,
                document=embedded_doc,
                sparse_vectors=sparse_vectors,
            )
            logger.info(
                "Document points successfully indexed in Qdrant",
                document_id=document_id,
                points_written=points_written,
            )

        # 9. Update DB record to indexed
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                doc.status = "indexed"
                await session.commit()

        logger.info("Document successfully marked as indexed", document_id=document_id)
        return True

    except Exception as exc:
        logger.exception(
            "In-process document indexing failed",
            document_id=document_id,
            error=str(exc),
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc:
                if attempt >= MAX_INDEXING_RETRIES:
                    doc.status = "failed"
                    logger.error(
                        "Document indexing permanently failed after max retries",
                        document_id=document_id,
                        attempts=attempt,
                        error=str(exc),
                    )
                else:
                    doc.status = f"uploaded:retry:{attempt + 1}"
                    logger.warning(
                        "Document indexing failed; bounded retry queued",
                        document_id=document_id,
                        next_attempt=attempt + 1,
                        max_retries=MAX_INDEXING_RETRIES,
                        error=str(exc),
                    )
                await session.commit()
        return False

    finally:
        if vector_store is not None:
            try:
                await vector_store.close()
            except Exception:
                pass


async def recover_stale_processing_documents() -> list[str]:
    """
    Recovers documents stranded in 'processing' status after a container restart, crash, or OOM.
    Requeues stranded documents if within retry limits, or marks them 'failed' if max retries exceeded.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_PROCESSING_TIMEOUT_SECONDS)
    recovered_ids: list[str] = []

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document).where(
                    Document.status.like("processing%"),
                    Document.updated_at < cutoff,
                )
            )
            stale_docs = result.scalars().all()
            for doc in stale_docs:
                attempt = 1
                if ":attempt:" in doc.status:
                    try:
                        attempt = int(doc.status.split(":")[-1])
                    except ValueError:
                        attempt = 1

                if attempt >= MAX_INDEXING_RETRIES:
                    doc.status = "failed"
                    logger.error(
                        "Stale document exceeded max processing retries across restarts; marked permanently failed",
                        document_id=doc.id,
                        attempts=attempt,
                    )
                else:
                    next_attempt = attempt + 1
                    doc.status = f"uploaded:retry:{next_attempt}"
                    logger.warning(
                        "Requeued stranded document for retry after restart/timeout",
                        document_id=doc.id,
                        next_attempt=next_attempt,
                    )
                    recovered_ids.append(doc.id)

            if stale_docs:
                await session.commit()
                logger.info(
                    "Processed stale in-flight documents",
                    count=len(stale_docs),
                    recovered=len(recovered_ids),
                )

    except Exception as exc:
        logger.warning("Error recovering stale processing documents", error=str(exc))

    return recovered_ids


async def sync_unindexed_documents() -> list[str]:
    """
    Scans PostgreSQL for unindexed documents and triggers indexing for each.
    1. Durably recovers any documents stranded in 'processing' state across restarts.
    2. Indexes documents in 'uploaded' or 'uploaded:retry:*' status.
    3. Prevents duplicate in-flight processing and enforces bounded retries.
    """
    logger.info("Scanning for unindexed uploaded documents...")

    # 1. Recover any documents stranded from a previous crash/restart
    await recover_stale_processing_documents()

    unindexed_ids: list[str] = []
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Document.id).where(
                    or_(
                        Document.status == "uploaded",
                        Document.status.like("uploaded:retry:%"),
                    )
                )
            )
            unindexed_ids = [row[0] for row in result.fetchall()]

        # Filter out documents already being indexed in-process
        pending_ids = [
            doc_id for doc_id in unindexed_ids if doc_id not in _active_indexing_ids
        ]

        if not pending_ids:
            logger.info("No unindexed documents found")
            return []

        logger.info(
            "Found unindexed documents to process",
            count=len(pending_ids),
            ids=pending_ids,
        )
        successful_ids = []
        for doc_id in pending_ids:
            if doc_id in _active_indexing_ids:
                continue
            _active_indexing_ids.add(doc_id)
            try:
                success = await index_document_pipeline(doc_id)
                if success:
                    successful_ids.append(doc_id)
            finally:
                _active_indexing_ids.discard(doc_id)

        return successful_ids

    except Exception as exc:
        logger.warning("Error while syncing unindexed documents", error=str(exc))
        return []
