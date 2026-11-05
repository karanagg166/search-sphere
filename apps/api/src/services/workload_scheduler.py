import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import HTTPException, status

from src.services.memory_guard import memory_guard
from src.services.model_manager import model_manager

logger = structlog.get_logger()

# Maximum concurrency bounds tailored to Render Free Tier (512 MiB RAM, 0.5 CPU)
MAX_CONCURRENT_RAG_QUERIES = 2
MAX_CONCURRENT_HEAVY_JOBS = 1
DEFAULT_RAG_QUEUE_TIMEOUT_SEC = 8.0
DEFAULT_HEAVY_QUEUE_TIMEOUT_SEC = 5.0


class WorkloadOverloadError(HTTPException):
    """HTTP 429 / 503 response with standard Retry-After header during capacity overload."""

    def __init__(self, detail: str, retry_after: int = 5, status_code: int = status.HTTP_429_TOO_MANY_REQUESTS) -> None:
        super().__init__(
            status_code=status_code,
            detail=detail,
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


class WorkloadScheduler:
    """
    Centralized Memory-Aware Admission Controller and Concurrency Limiter.

    Prevents simultaneous execution of memory-heavy tasks (e.g. PDF parsing + RAG answering),
    bounds concurrent thread/async task creation, and sheds excess traffic with
    clean HTTP 429 backpressure and Retry-After headers instead of OOM crashing.
    """

    def __init__(
        self,
        max_rag_queries: int = MAX_CONCURRENT_RAG_QUERIES,
        max_heavy_jobs: int = MAX_CONCURRENT_HEAVY_JOBS,
    ) -> None:
        self.max_rag_queries = max_rag_queries
        self.max_heavy_jobs = max_heavy_jobs
        self._rag_semaphore = asyncio.Semaphore(max_rag_queries)
        self._heavy_semaphore = asyncio.Semaphore(max_heavy_jobs)

        self._active_rag_queries: int = 0
        self._active_heavy_jobs: int = 0
        self._total_rag_served: int = 0
        self._total_heavy_served: int = 0
        self._total_rejected: int = 0

    @property
    def active_rag_queries(self) -> int:
        return self._active_rag_queries

    @property
    def active_heavy_jobs(self) -> int:
        return self._active_heavy_jobs

    @asynccontextmanager
    async def interactive_rag_scope(
        self,
        timeout: float = DEFAULT_RAG_QUEUE_TIMEOUT_SEC,
    ) -> AsyncIterator[None]:
        """
        Guards interactive RAG queries (search, answering, reranking).
        Ensures strict concurrency bounding (max 2) and immediate backpressure
        if memory pressure reaches critical levels.
        """
        # 1. Proactive memory check
        if not memory_guard.is_interactive_rag_allowed():
            model_manager.release_memory()
            if not memory_guard.is_interactive_rag_allowed():
                self._total_rejected += 1
                logger.warning(
                    "Rejected interactive RAG query due to critical memory pressure",
                    current_mb=round(memory_guard.get_current_memory_mb(), 1),
                    pressure=memory_guard.get_pressure_level(),
                )
                raise WorkloadOverloadError(
                    detail="Search service is currently managing heavy memory load. Please retry in a few seconds.",
                    retry_after=5,
                )

        # 2. Acquire concurrency slot with bounded timeout
        try:
            await asyncio.wait_for(self._rag_semaphore.acquire(), timeout=timeout)
        except TimeoutError:
            self._total_rejected += 1
            logger.warning(
                "Rejected interactive RAG query due to concurrency queue timeout",
                active_rag=self._active_rag_queries,
                timeout=timeout,
            )
            raise WorkloadOverloadError(
                detail="Search service is currently at maximum capacity. Please retry in a few seconds.",
                retry_after=3,
            ) from None

        self._active_rag_queries += 1
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._active_rag_queries = max(0, self._active_rag_queries - 1)
            self._total_rag_served += 1
            self._rag_semaphore.release()
            duration_ms = (time.perf_counter() - t0) * 1000
            # If memory is high after query, run compact
            if memory_guard.get_pressure_level() in ("high", "critical"):
                model_manager.release_memory()
            logger.debug(
                "Interactive RAG query completed",
                duration_ms=round(duration_ms, 1),
                remaining_active=self._active_rag_queries,
            )

    @asynccontextmanager
    async def heavy_job_scope(
        self,
        timeout: float = DEFAULT_HEAVY_QUEUE_TIMEOUT_SEC,
    ) -> AsyncIterator[None]:
        """
        Guards heavy document extraction, semantic chunking, and embedding indexing.
        Strictly limits concurrency to 1 and blocks when interactive queries or memory pressure is high.
        """
        # 1. Check memory headroom before admitting heavy work
        if not memory_guard.is_heavy_workload_allowed():
            try:
                model_manager.unload_model("reranker")
            except Exception:
                pass
            model_manager.release_memory()
            if not memory_guard.is_heavy_workload_allowed():
                self._total_rejected += 1
                logger.warning(
                    "Deferred or rejected heavy job due to memory pressure",
                    current_mb=round(memory_guard.get_current_memory_mb(), 1),
                    pressure=memory_guard.get_pressure_level(),
                )
                raise WorkloadOverloadError(
                    detail="Document processing deferred: backend is currently under memory pressure.",
                    retry_after=10,
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        # 2. Acquire concurrency slot (max 1 heavy job at a time)
        try:
            await asyncio.wait_for(self._heavy_semaphore.acquire(), timeout=timeout)
        except TimeoutError:
            self._total_rejected += 1
            logger.warning("Heavy job concurrency queue timeout", timeout=timeout)
            raise WorkloadOverloadError(
                detail="Another document is currently being indexed. Your request has been queued.",
                retry_after=10,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from None

        self._active_heavy_jobs += 1
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self._active_heavy_jobs = max(0, self._active_heavy_jobs - 1)
            self._total_heavy_served += 1
            self._heavy_semaphore.release()
            duration_ms = (time.perf_counter() - t0) * 1000
            # Always clean up memory immediately after a heavy document indexing job finishes
            model_manager.release_memory()
            logger.info(
                "Heavy document processing completed",
                duration_ms=round(duration_ms, 1),
                remaining_active=self._active_heavy_jobs,
                current_memory_mb=round(memory_guard.get_current_memory_mb(), 1),
            )

    def get_status(self) -> dict[str, Any]:
        """Returns workload metrics for monitoring and diagnostics."""
        return {
            "active_rag_queries": self._active_rag_queries,
            "max_rag_queries": self.max_rag_queries,
            "active_heavy_jobs": self._active_heavy_jobs,
            "max_heavy_jobs": self.max_heavy_jobs,
            "total_rag_served": self._total_rag_served,
            "total_heavy_served": self._total_heavy_served,
            "total_rejected": self._total_rejected,
            "memory": memory_guard.get_metrics(),
        }


# Global Singleton
workload_scheduler = WorkloadScheduler()
