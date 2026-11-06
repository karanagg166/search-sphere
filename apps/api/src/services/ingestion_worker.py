"""Single-concurrency PostgreSQL ingestion for every document source."""

import asyncio
import time
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from src.db import AsyncSessionLocal
from src.inference.policy import (
    ProviderError,
    ProviderNotApprovedError,
    authorize_cohere,
    inference_scope,
)
from src.models.ingestion_job import IngestionJob
from src.processing.extraction.document_extractor import DocumentExtractionError
from src.repositories.ingestion_queue import IngestionQueue
from src.services.cleanup_worker import DocumentCleanupWorker
from src.services.ingestion_pipeline import process_ingestion
from src.services.ingestion_publication import publish_ingestion
from src.services.ingestion_recovery import adopt_legacy_pending
from src.services.ingestion_source import (
    IngestionSourceError,
    load_ingestion_source,
    reauthorize_ingestion,
)
from src.storage.object_storage import get_object_storage

logger = structlog.get_logger()


class LeaseLostError(RuntimeError):
    pass


class PostgresIngestionWorker:
    def __init__(self, session_factory=AsyncSessionLocal, processor=process_ingestion, storage_factory=get_object_storage):
        self.sessions = session_factory
        self.queue = IngestionQueue(session_factory)
        self.processor, self.storage_factory = processor, storage_factory
        self._task = None
        self._wake = asyncio.Event()
        self.last_poll_at = None
        self.last_error_code = None
        self._last_adoption = 0

    def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def notify_new_job(self):
        self._wake.set()

    async def _run(self):
        await asyncio.sleep(1)
        while True:
            try:
                self.last_poll_at = datetime.now(UTC)
                self.last_error_code = None
                if time.monotonic() - self._last_adoption > 60:
                    self._last_adoption = time.monotonic()
                    await adopt_legacy_pending(self.sessions, self.queue)
                await DocumentCleanupWorker(self.sessions, self.storage_factory).run_once()
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.last_error_code = "queue_unavailable"
                logger.warning("Ingestion polling failed", error_type=type(error).__name__)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=4)
            except TimeoutError:
                pass
            self._wake.clear()

    async def run_once(self):
        lease = await self.queue.claim()
        if lease is None:
            return False
        stopped, lost = asyncio.Event(), asyncio.Event()

        async def guard():
            if stopped.is_set() or lost.is_set():
                raise ProviderNotApprovedError("Ingestion lease is no longer active.")
            async with self.sessions() as db:
                live = (await db.execute(select(IngestionJob.id).where(*self.queue.lease_predicate(lease)))).scalar_one_or_none()
            if live is None:
                raise ProviderNotApprovedError("Ingestion lease is no longer active.")
            await reauthorize_ingestion(self.sessions, source)

        async def heartbeat():
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=20)
                except TimeoutError:
                    try:
                        if not await self.queue.heartbeat(lease):
                            lost.set()
                            return
                    except Exception:
                        lost.set()
                        return

        async def stage(value):
            if lost.is_set() or not await self.queue.stage(lease, value):
                raise LeaseLostError

        pulse = asyncio.create_task(heartbeat())
        try:
            async with self.sessions() as db:
                source = await load_ingestion_source(db, lease.document_kind, lease.document_id)
            with inference_scope(source.classification, source.client_id, source.tenant_id, validator=guard):
                from src.config import settings

                authorize_cohere(settings.COHERE_API_KEY or "")  # Fail before downloading unapproved content.
                processed = await self.processor(source, self.storage_factory(), stage)
                await stage("indexing")
                await guard()
                await publish_ingestion(self.sessions, self.queue, lease, source, processed)
        except asyncio.CancelledError:
            raise  # Lease expiry recovers work after a process/container interruption.
        except LeaseLostError:
            pass
        except (ProviderError, IngestionSourceError) as error:
            await self.queue.fail(lease, error.code, retryable=error.retryable)
        except DocumentExtractionError:
            await self.queue.fail(lease, "invalid_document", retryable=False)
        except Exception as error:
            logger.warning("Ingestion attempt failed", job_id=lease.job_id, error_type=type(error).__name__)
            await self.queue.fail(lease, "processing_failed", retryable=True)
        finally:
            stopped.set()
            await pulse
        return True


ingestion_worker = PostgresIngestionWorker()


async def serve_worker():
    """Independent worker role using the same hosted-only image and PostgreSQL queue."""
    import signal

    from src.db import engine

    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for termination_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(termination_signal, stopping.set)
    ingestion_worker.start()
    try:
        await stopping.wait()
    finally:
        await ingestion_worker.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(serve_worker())
