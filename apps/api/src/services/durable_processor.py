import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import or_, select, update

from src.db import AsyncSessionLocal
from src.models.document import Document
from src.processing.chunking import DocumentChunker
from src.processing.cleaning import TextCleaner
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.processing.extraction.document_extractor import (
    DocumentExtractionError,
    DocumentExtractor,
)
from src.processing.sparse_embedding.bm25_embedder import BM25Embedder
from src.services.memory_guard import memory_guard
from src.services.model_manager import model_manager
from src.services.workload_scheduler import workload_scheduler
from src.storage.object_storage import get_object_storage
from src.vector_store import get_vector_store

logger = structlog.get_logger()

MAX_INDEXING_RETRIES = 3
LEASE_TIMEOUT_SECONDS = 300  # 5 minutes
POLL_INTERVAL_SECONDS = 4.0


class DurableDocumentProcessor:
    """
    $0-Infrastructure Durable Document Queue & Processor.

    Operates over PostgreSQL without requiring a paid RabbitMQ/Redis broker.
    Enforces atomic job leases, bounded retries, dead-letter failure states,
    and single-job concurrency under 512 MiB memory limits.
    """

    def __init__(self) -> None:
        self._wake_event = asyncio.Event()
        self._running: bool = False
        self._worker_task: asyncio.Task | None = None
        self._active_doc_id: str | None = None

    def notify_new_job(self) -> None:
        """Signals the background processor that a new document upload is ready."""
        self._wake_event.set()

    def start(self) -> None:
        """Launches the background polling loop as an asyncio Task."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._run_loop())
            logger.info("DurableDocumentProcessor background worker started")

    async def stop(self) -> None:
        """Gracefully terminates background processing loop."""
        self._running = False
        self._wake_event.set()
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("DurableDocumentProcessor background worker stopped")

    async def _run_loop(self) -> None:
        """Sequential single-document background processing loop."""
        # Initial yield so Uvicorn binds port and answers readiness probes immediately
        await asyncio.sleep(2.0)

        # 1. Recover stranded jobs from previous restart/crash
        await self.recover_stranded_jobs()

        while self._running:
            try:
                # If memory is high or interactive queries are active, yield and wait
                if not memory_guard.is_heavy_workload_allowed() or workload_scheduler.active_rag_queries > 0:
                    await asyncio.sleep(2.0)
                    continue

                claimed_job = await self.claim_next_job()
                if claimed_job:
                    doc_id, storage_key, attempt = claimed_job
                    self._active_doc_id = doc_id
                    try:
                        await self.process_job(doc_id, storage_key, attempt)
                    finally:
                        self._active_doc_id = None
                        # Allow event loop and other requests to interleave smoothly
                        await asyncio.sleep(0.5)
                else:
                    # No pending jobs: wait on wake event or poll timeout
                    try:
                        await asyncio.wait_for(self._wake_event.wait(), timeout=POLL_INTERVAL_SECONDS)
                    except TimeoutError:
                        pass
                    self._wake_event.clear()

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Unexpected error in durable processor loop", error=str(exc))
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

    async def claim_next_job(self) -> tuple[str, str, int] | None:
        """
        Atomically claims one pending document for processing using optimistic locking.
        Returns (doc_id, storage_key, attempt) if claimed, or None.
        """
        async with AsyncSessionLocal() as session:
            # 1. Select the oldest pending document
            stmt = (
                select(Document)
                .where(
                    or_(
                        Document.status == "uploaded",
                        Document.status.like("uploaded:retry:%"),
                    )
                )
                .order_by(Document.created_at.asc())
                .limit(1)
            )
            result = await session.execute(stmt)
            doc = result.scalar_one_or_none()
            if not doc:
                return None

            # Parse attempt
            attempt = 1
            if ":retry:" in doc.status:
                try:
                    attempt = int(doc.status.split(":")[-1])
                except ValueError:
                    attempt = 1

            if attempt > MAX_INDEXING_RETRIES:
                doc.status = "failed:max_retries_exceeded"
                await session.commit()
                logger.warning(
                    "Document exceeded max indexing retries; marked terminal failed",
                    document_id=doc.id,
                    attempt=attempt,
                )
                return None

            # 2. Atomic claim update
            lease_token = f"proc:{int(time.time())}:{attempt}"
            old_status = doc.status
            update_stmt = (
                update(Document)
                .where(Document.id == doc.id, Document.status == old_status)
                .values(status=lease_token, updated_at=datetime.now(UTC))
            )
            update_result = await session.execute(update_stmt)
            await session.commit()

            if update_result.rowcount == 1:
                logger.info(
                    "Atomically claimed document indexing lease",
                    document_id=doc.id,
                    lease=lease_token,
                    attempt=attempt,
                )
                return (doc.id, doc.storage_key, attempt)

            return None

    async def process_job(self, document_id: str, storage_key: str, attempt: int) -> bool:
        """
        Executes full multimodal extraction, chunking, embedding, and Qdrant synchronization
        under memory-aware scheduler guardrails.
        """
        logger.info("Executing durable document processing", document_id=document_id, attempt=attempt)
        storage = get_object_storage()
        vector_store = None

        try:
            async with workload_scheduler.heavy_job_scope():
                # Step 1: Download raw PDF bytes from object storage
                try:
                    pdf_bytes = await storage.download(storage_key)
                except Exception as dl_err:
                    logger.error(
                        "Failed to download storage object",
                        document_id=document_id,
                        storage_key=storage_key,
                        error=str(dl_err),
                    )
                    await self._mark_failure(
                        document_id,
                        attempt,
                        failure_status="failed:missing_storage_file",
                        error_msg=f"Storage object not found: {dl_err}",
                    )
                    return False

                if not pdf_bytes:
                    await self._mark_failure(
                        document_id,
                        attempt,
                        failure_status="failed:missing_storage_file",
                        error_msg="Downloaded storage bytes are empty.",
                    )
                    return False

                # Step 2: Multimodal PDF text and image extraction
                try:
                    extractor = DocumentExtractor()
                    extracted_doc = await asyncio.to_thread(extractor.extract, pdf_bytes)
                except DocumentExtractionError as ext_err:
                    logger.error("Document extraction failed", document_id=document_id, error=str(ext_err))
                    await self._mark_failure(
                        document_id,
                        attempt,
                        failure_status="failed:corrupted_pdf",
                        error_msg=str(ext_err),
                    )
                    return False

                # Release raw PDF bytes from memory immediately
                del pdf_bytes

                # Step 3: Text cleaning
                cleaner = TextCleaner()
                cleaned_doc = cleaner.clean_document(extracted_doc)
                del extracted_doc

                # Step 4: Semantic chunking (reusing centralized ModelManager embedder)
                chunker = DocumentChunker()
                chunked_doc = await asyncio.to_thread(chunker.chunk_document, cleaned_doc)
                chunk_count = len(chunked_doc.chunks)
                del cleaned_doc

                if chunk_count > 0:
                    # Step 5: Dense embeddings (bounded batching)
                    embedder = DenseEmbedder()
                    embedded_doc = await asyncio.to_thread(embedder.embed_document, chunked_doc)

                    # Step 6: Sparse lexical BM25 embeddings
                    sparse_embedder = BM25Embedder()
                    sparse_vectors = await asyncio.to_thread(sparse_embedder.embed_chunks, chunked_doc.chunks)

                    # Step 7: Vector index synchronization
                    vector_store = get_vector_store(sparse_embedder=sparse_embedder)
                    points_written = await vector_store.index_document(
                        document_id=document_id,
                        document=embedded_doc,
                        sparse_vectors=sparse_vectors,
                    )
                    logger.info(
                        "Document successfully indexed into vector store",
                        document_id=document_id,
                        points=points_written,
                    )

                # Step 8: Atomically update DB record to 'indexed'
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        update(Document)
                        .where(Document.id == document_id)
                        .values(
                            status="indexed",
                            chunk_count=chunk_count,
                            updated_at=datetime.now(UTC),
                        )
                    )
                    await session.commit()

                logger.info("Document successfully marked as indexed", document_id=document_id)
                return True

        except Exception as exc:
            logger.exception("Error during document indexing execution", document_id=document_id, error=str(exc))
            await self._mark_failure(
                document_id,
                attempt,
                failure_status=f"uploaded:retry:{attempt + 1}" if attempt < MAX_INDEXING_RETRIES else "failed:max_retries_exceeded",
                error_msg=str(exc),
            )
            return False

        finally:
            if vector_store is not None:
                try:
                    await vector_store.close()
                except Exception:
                    pass
            model_manager.release_memory()

    async def _mark_failure(
        self,
        document_id: str,
        attempt: int,
        failure_status: str,
        error_msg: str,
    ) -> None:
        """Persists retry or terminal failure state atomically in PostgreSQL."""
        try:
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(status=failure_status, updated_at=datetime.now(UTC))
                )
                await session.commit()
            logger.warning(
                "Document state updated on failure",
                document_id=document_id,
                status=failure_status,
                attempt=attempt,
                error=error_msg,
            )
        except Exception as db_err:
            logger.error("Failed to update document status in database", error=str(db_err))

    async def recover_stranded_jobs(self) -> list[str]:
        """
        Durably recovers documents stranded in 'proc:%' or 'processing:%' state
        from container restarts, OOM crashes, or deploy interruptions.
        """
        cutoff = datetime.now(UTC) - timedelta(seconds=LEASE_TIMEOUT_SECONDS)
        recovered_ids: list[str] = []

        try:
            async with AsyncSessionLocal() as session:
                stmt = select(Document).where(
                    or_(
                        Document.status.like("proc:%"),
                        Document.status.like("processing%"),
                    ),
                    Document.updated_at < cutoff,
                )
                result = await session.execute(stmt)
                stale_docs = result.scalars().all()

                for doc in stale_docs:
                    attempt = 1
                    parts = doc.status.split(":")
                    if len(parts) >= 3 and parts[-1].isdigit():
                        attempt = int(parts[-1])

                    if attempt >= MAX_INDEXING_RETRIES:
                        doc.status = "failed:max_retries_exceeded"
                        logger.error(
                            "Stranded document exceeded max retries across restarts; marked terminal failed",
                            document_id=doc.id,
                            attempts=attempt,
                        )
                    else:
                        next_attempt = attempt + 1
                        doc.status = f"uploaded:retry:{next_attempt}"
                        logger.warning(
                            "Requeued stranded document lease after container restart/timeout",
                            document_id=doc.id,
                            next_attempt=next_attempt,
                        )
                        recovered_ids.append(doc.id)

                if stale_docs:
                    await session.commit()
                    logger.info(
                        "Stranded document recovery completed",
                        total_stranded=len(stale_docs),
                        requeued=len(recovered_ids),
                    )

        except Exception as exc:
            logger.warning("Error recovering stranded documents", error=str(exc))

        return recovered_ids

    def get_status(self) -> dict[str, Any]:
        """Provides status metadata for diagnostics and monitoring."""
        return {
            "worker_running": self._running,
            "active_doc_id": self._active_doc_id,
            "max_retries": MAX_INDEXING_RETRIES,
            "lease_timeout_sec": LEASE_TIMEOUT_SECONDS,
        }


# Global Singleton
durable_processor = DurableDocumentProcessor()


def get_durable_processor() -> DurableDocumentProcessor:
    """Dependency helper returning the global DurableDocumentProcessor singleton."""
    return durable_processor
