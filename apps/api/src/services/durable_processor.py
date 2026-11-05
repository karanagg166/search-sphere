import asyncio
import time
import uuid
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

    def __init__(self, worker_id: str | None = None) -> None:
        self.worker_id: str = worker_id or f"w_{uuid.uuid4().hex[:8]}"
        self.heartbeat_interval: float = 20.0
        self._wake_event = asyncio.Event()
        self._running: bool = False
        self._worker_task: asyncio.Task | None = None
        self._active_doc_id: str | None = None
        self._last_recovery_time: float = 0.0
        self._recovery_interval: float = 60.0  # Periodic sweep every 60s

    def notify_new_job(self) -> None:
        """Signals the background processor that a new document upload is ready."""
        self._wake_event.set()

    def start(self) -> None:
        """Launches the background polling loop as an asyncio Task."""
        if not self._running:
            self._running = True
            self._worker_task = asyncio.create_task(self._run_loop())
            logger.info("DurableDocumentProcessor background worker started", worker_id=self.worker_id)

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
        logger.info("DurableDocumentProcessor background worker stopped", worker_id=self.worker_id)

    async def _run_loop(self) -> None:
        """Sequential single-document background processing loop."""
        # Initial yield so Uvicorn binds port and answers readiness probes immediately
        await asyncio.sleep(2.0)

        # 1. Recover stranded jobs from previous restart/crash safely (expired leases only)
        # Avoid force_all=True so living workers during deployment handoffs are never stolen
        await self.recover_stranded_jobs(force_all=False)
        self._last_recovery_time = time.monotonic()

        while self._running:
            try:
                # If memory is high or interactive queries are active, yield and wait
                if not memory_guard.is_heavy_workload_allowed() or workload_scheduler.active_rag_queries > 0:
                    await asyncio.sleep(2.0)
                    continue

                claimed_job = await self.claim_next_job()
                if claimed_job:
                    doc_id, storage_key, attempt, lease_token = claimed_job
                    self._active_doc_id = doc_id
                    try:
                        await self.process_job(doc_id, storage_key, attempt, lease_token=lease_token)
                    finally:
                        self._active_doc_id = None
                        # Allow event loop and other requests to interleave smoothly
                        await asyncio.sleep(0.5)
                else:
                    # Periodically check for stranded jobs when idle
                    now = time.monotonic()
                    if now - self._last_recovery_time > self._recovery_interval:
                        await self.recover_stranded_jobs(force_all=False)
                        self._last_recovery_time = now

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

    async def claim_next_job(self) -> tuple[str, str, int, str] | None:
        """
        Atomically claims one pending document for processing using optimistic locking.
        Returns (doc_id, storage_key, attempt, lease_token) if claimed, or None.
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
                fail_stmt = (
                    update(Document)
                    .where(Document.id == doc.id, Document.status == doc.status)
                    .values(status="failed:max_retries_exceeded", updated_at=datetime.now(UTC))
                )
                await session.execute(fail_stmt)
                await session.commit()
                logger.warning(
                    "Document exceeded max indexing retries; marked terminal failed",
                    document_id=doc.id,
                    attempt=attempt,
                )
                return None

            # 2. Atomic claim update with worker identity and lease expiration epoch
            expires_epoch = int(time.time() + LEASE_TIMEOUT_SECONDS)
            lease_token = f"proc:{self.worker_id}:{expires_epoch}:{attempt}"
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
                    worker_id=self.worker_id,
                )
                return (doc.id, doc.storage_key, attempt, lease_token)

            return None

    async def complete_job(self, document_id: str, chunk_count: int, expected_lease: str) -> bool:
        """Atomically marks a job indexed only if this worker still holds the lease."""
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    update(Document)
                    .where(Document.id == document_id, Document.status == expected_lease)
                    .values(
                        status="indexed",
                        chunk_count=chunk_count,
                        updated_at=datetime.now(UTC),
                    )
                )
                res = await session.execute(stmt)
                await session.commit()
                if res.rowcount == 1:
                    logger.info("Document successfully marked as indexed", document_id=document_id)
                    return True
                logger.warning(
                    "Stale worker prevented from overwriting document status",
                    document_id=document_id,
                    worker_id=self.worker_id,
                    lease=expected_lease,
                )
                return False
        except Exception as exc:
            logger.error("Failed to atomically complete document job", error=str(exc))
            return False

    async def heartbeat(self, document_id: str, current_lease: str, attempt: int) -> str | None:
        """
        Extends lease expiration in PostgreSQL using compare-and-set.
        Returns the new lease token, or None if the lease was lost or stolen.
        """
        new_expires = int(time.time() + LEASE_TIMEOUT_SECONDS)
        new_lease = f"proc:{self.worker_id}:{new_expires}:{attempt}"
        try:
            async with AsyncSessionLocal() as session:
                stmt = (
                    update(Document)
                    .where(Document.id == document_id, Document.status == current_lease)
                    .values(status=new_lease, updated_at=datetime.now(UTC))
                )
                res = await session.execute(stmt)
                await session.commit()
                if res.rowcount == 1:
                    return new_lease
                logger.warning(
                    "Heartbeat lease extension failed: lease no longer held",
                    document_id=document_id,
                    current_lease=current_lease,
                    worker_id=self.worker_id,
                )
                return None
        except Exception as exc:
            logger.warning("Heartbeat DB error", error=str(exc))
            return None

    async def _run_heartbeat(
        self,
        document_id: str,
        initial_lease: str,
        attempt: int,
        stop_event: asyncio.Event,
        lease_holder: list[str],
        cancelled_event: asyncio.Event,
    ) -> None:
        """Periodically refreshes PostgreSQL lease to prove worker liveness."""
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.heartbeat_interval)
                break
            except TimeoutError:
                if stop_event.is_set():
                    break
                current_token = lease_holder[0]
                new_token = await self.heartbeat(document_id, current_token, attempt)
                if new_token:
                    lease_holder[0] = new_token
                else:
                    cancelled_event.set()
                    break

    async def process_job(
        self,
        document_id: str,
        storage_key: str,
        attempt: int,
        lease_token: str | None = None,
    ) -> bool:
        """
        Executes full multimodal extraction, chunking, embedding, and vector synchronization
        under memory-aware scheduler guardrails and distributed lease protection.
        """
        current_lease = lease_token or f"proc:{self.worker_id}:{int(time.time() + LEASE_TIMEOUT_SECONDS)}:{attempt}"
        logger.info(
            "Executing durable document processing",
            document_id=document_id,
            attempt=attempt,
            worker_id=self.worker_id,
            lease=current_lease,
        )
        storage = get_object_storage()
        vector_store = None

        stop_heartbeat = asyncio.Event()
        lease_cancelled = asyncio.Event()
        lease_holder = [current_lease]
        heartbeat_task = asyncio.create_task(
            self._run_heartbeat(
                document_id,
                current_lease,
                attempt,
                stop_heartbeat,
                lease_holder,
                lease_cancelled,
            )
        )

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
                        expected_lease=lease_holder[0],
                    )
                    return False

                if not pdf_bytes:
                    await self._mark_failure(
                        document_id,
                        attempt,
                        failure_status="failed:missing_storage_file",
                        error_msg="Downloaded storage bytes are empty.",
                        expected_lease=lease_holder[0],
                    )
                    return False

                if lease_cancelled.is_set():
                    logger.warning("Lease cancelled before extraction; aborting job", document_id=document_id)
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
                        expected_lease=lease_holder[0],
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

                if lease_cancelled.is_set():
                    logger.warning("Lease cancelled before embedding; aborting job", document_id=document_id)
                    return False

                if chunk_count > 0:
                    # Step 5: Dense embeddings (bounded batching)
                    embedder = DenseEmbedder()
                    embedded_doc = await asyncio.to_thread(embedder.embed_document, chunked_doc)

                    # Step 6: Sparse lexical BM25 embeddings
                    sparse_embedder = BM25Embedder()
                    sparse_vectors = await asyncio.to_thread(sparse_embedder.embed_chunks, chunked_doc.chunks)

                    if lease_cancelled.is_set():
                        logger.warning("Lease cancelled before vector store write; aborting job", document_id=document_id)
                        return False

                    # Step 7: Vector index synchronization (idempotent: cleans up previous chunks first)
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

                # Step 8: Atomically update DB record to 'indexed' using CAS against current lease
                success = await self.complete_job(document_id, chunk_count, expected_lease=lease_holder[0])
                return success

        except Exception as exc:
            logger.exception("Error during document indexing execution", document_id=document_id, error=str(exc))
            await self._mark_failure(
                document_id,
                attempt,
                failure_status=f"uploaded:retry:{attempt + 1}" if attempt < MAX_INDEXING_RETRIES else "failed:max_retries_exceeded",
                error_msg=str(exc),
                expected_lease=lease_holder[0],
            )
            return False

        finally:
            stop_heartbeat.set()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
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
        expected_lease: str | None = None,
    ) -> bool:
        """Persists retry or terminal failure state atomically in PostgreSQL."""
        try:
            async with AsyncSessionLocal() as session:
                stmt = update(Document).where(Document.id == document_id)
                if expected_lease:
                    stmt = stmt.where(Document.status == expected_lease)
                stmt = stmt.values(status=failure_status, updated_at=datetime.now(UTC))
                res = await session.execute(stmt)
                await session.commit()
                if expected_lease and res.rowcount == 0:
                    logger.warning(
                        "Stale worker prevented from marking failure on lost lease",
                        document_id=document_id,
                        worker_id=self.worker_id,
                        expected_lease=expected_lease,
                    )
                    return False
            logger.warning(
                "Document state updated on failure",
                document_id=document_id,
                status=failure_status,
                attempt=attempt,
                error=error_msg,
            )
            return True
        except Exception as db_err:
            logger.error("Failed to update document status in database", error=str(db_err))
            return False

    async def recover_stranded_jobs(self, force_all: bool = False) -> list[str]:
        """
        Durably recovers documents stranded in 'proc:%' or 'processing:%' state
        from container restarts, OOM crashes, or deploy interruptions.
        Uses Compare-and-Set updates to ensure multiple concurrent workers cannot race.
        If force_all=False, recovers ONLY leases whose expiration epoch has passed
        or whose updated_at exceeds LEASE_TIMEOUT_SECONDS, safely preserving active work
        during rolling deployments and handoffs.
        """
        now_epoch = int(time.time())
        cutoff = datetime.now(UTC) - timedelta(seconds=LEASE_TIMEOUT_SECONDS)
        recovered_ids: list[str] = []

        try:
            async with AsyncSessionLocal() as session:
                base_filter = or_(
                    Document.status.like("proc:%"),
                    Document.status.like("processing%"),
                )
                stmt = select(Document).where(base_filter)
                result = await session.execute(stmt)
                stale_docs = result.scalars().all()

                for doc in stale_docs:
                    parts = doc.status.split(":")
                    attempt = 1
                    is_expired = force_all

                    if len(parts) >= 4 and parts[2].isdigit():
                        # proc:{worker_id}:{expires_epoch}:{attempt}
                        expires_epoch = int(parts[2])
                        if parts[3].isdigit():
                            attempt = int(parts[3])
                        if not force_all:
                            is_expired = (now_epoch >= expires_epoch)
                    elif len(parts) >= 3 and parts[1].isdigit():
                        # legacy proc:{epoch}:{attempt}
                        created_epoch = int(parts[1])
                        if parts[2].isdigit():
                            attempt = int(parts[2])
                        if not force_all:
                            is_expired = ((now_epoch - created_epoch) >= LEASE_TIMEOUT_SECONDS)
                    elif doc.status.startswith("processing"):
                        if not force_all:
                            is_expired = (doc.updated_at < cutoff)

                    if not is_expired:
                        continue

                    # Expired lease: atomically reclaim using CAS
                    if attempt >= MAX_INDEXING_RETRIES:
                        next_status = "failed:max_retries_exceeded"
                    else:
                        next_attempt = attempt + 1
                        next_status = f"uploaded:retry:{next_attempt}"

                    update_stmt = (
                        update(Document)
                        .where(Document.id == doc.id, Document.status == doc.status)
                        .values(status=next_status, updated_at=datetime.now(UTC))
                    )
                    update_res = await session.execute(update_stmt)
                    if update_res.rowcount == 1:
                        if next_status != "failed:max_retries_exceeded":
                            recovered_ids.append(doc.id)
                            logger.warning(
                                "Requeued expired document lease using atomic CAS",
                                document_id=doc.id,
                                previous_status=doc.status,
                                next_attempt=attempt + 1,
                            )
                        else:
                            logger.error(
                                "Expired document exceeded max retries across restarts; marked terminal failed",
                                document_id=doc.id,
                                attempts=attempt,
                            )

                if stale_docs:
                    await session.commit()
                    if recovered_ids:
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
            "worker_id": self.worker_id,
            "active_doc_id": self._active_doc_id,
            "max_retries": MAX_INDEXING_RETRIES,
            "lease_timeout_sec": LEASE_TIMEOUT_SECONDS,
            "heartbeat_interval_sec": self.heartbeat_interval,
        }


# Global Singleton
durable_processor = DurableDocumentProcessor()


def get_durable_processor() -> DurableDocumentProcessor:
    """Dependency helper returning the global DurableDocumentProcessor singleton."""
    return durable_processor
