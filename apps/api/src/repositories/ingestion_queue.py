"""PostgreSQL queue with atomic claims and fencing for all document sources."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import AsyncSessionLocal
from src.models.ingestion_job import IngestionJob

MAX_ATTEMPTS = 3
LEASE_SECONDS = 120


@dataclass(frozen=True)
class JobLease:
    job_id: str
    document_kind: str
    document_id: str
    token: str
    attempt: int


class IngestionQueue:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.sessions = session_factory

    async def enqueue(
        self, db: AsyncSession, kind: str, document_id: str, *, reset: bool = False
    ) -> IngestionJob:
        """Caller commits this outbox row together with document registration."""
        if kind not in {"standalone", "external"}:
            raise ValueError("Unsupported document kind")
        if db.get_bind().dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        await db.execute(
            insert(IngestionJob)
            .values(
                id=str(uuid.uuid4()),
                document_kind=kind,
                document_id=document_id,
                status="QUEUED",
                stage="registered",
                attempts=0,
                generation=1,
            )
            .on_conflict_do_nothing(index_elements=["document_kind", "document_id"])
        )
        job = (
            await db.execute(
                select(IngestionJob)
                .where(
                    IngestionJob.document_kind == kind,
                    IngestionJob.document_id == document_id,
                )
                .with_for_update()
            )
        ).scalar_one()
        if reset:
            job.status, job.stage, job.attempts = "QUEUED", "registered", 0
            job.generation += 1
            job.available_at = datetime.now(UTC)
            job.queued_at = datetime.now(UTC)
            job.lease_token = job.lease_expires_at = job.error_code = None
        return job

    async def claim(self) -> JobLease | None:
        async with self.sessions() as db, db.begin():
            # Expired work survives process/container loss. Live leases are untouched.
            expired = (
                await db.execute(
                    select(IngestionJob)
                    .where(
                        IngestionJob.status == "RUNNING",
                        IngestionJob.lease_expires_at <= func.now(),
                    )
                    .order_by(IngestionJob.lease_expires_at)
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
            ).scalars()
            for job in expired:
                job.lease_token = job.lease_expires_at = None
                job.status = "FAILED" if job.attempts >= MAX_ATTEMPTS else "QUEUED"
                job.stage = "failed" if job.status == "FAILED" else "recovering"
                job.error_code = "lease_expired" if job.status == "FAILED" else None
                await self.mirror_document_state(db, job)
            await db.flush()
            job = (
                await db.execute(
                    select(IngestionJob)
                    .where(
                        IngestionJob.status.in_(("QUEUED", "RETRY")),
                        IngestionJob.available_at <= func.now(),
                        IngestionJob.attempts < MAX_ATTEMPTS,
                    )
                    .order_by(IngestionJob.available_at, IngestionJob.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if not job:
                return None
            job.status, job.stage = "RUNNING", "claimed"
            job.attempts += 1
            job.lease_token = str(uuid.uuid4())
            job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=LEASE_SECONDS)
            await self.mirror_document_state(db, job)
            return JobLease(
                job.id,
                job.document_kind,
                job.document_id,
                job.lease_token,
                job.attempts,
            )

    @staticmethod
    def lease_predicate(lease: JobLease):
        return (
            IngestionJob.id == lease.job_id,
            IngestionJob.lease_token == lease.token,
            IngestionJob.status == "RUNNING",
            IngestionJob.lease_expires_at > func.now(),
        )

    async def heartbeat(self, lease: JobLease) -> bool:
        async with self.sessions() as db, db.begin():
            result = await db.execute(
                update(IngestionJob)
                .where(*self.lease_predicate(lease))
                .values(
                    lease_expires_at=datetime.now(UTC)
                    + timedelta(seconds=LEASE_SECONDS),
                )
            )
            return result.rowcount == 1

    async def stage(self, lease: JobLease, stage: str) -> bool:
        if stage not in {
            "downloading",
            "extracting",
            "chunking",
            "embedding",
            "indexing",
        }:
            raise ValueError("Unsupported ingestion stage")
        async with self.sessions() as db, db.begin():
            result = await db.execute(
                update(IngestionJob)
                .where(*self.lease_predicate(lease))
                .values(stage=stage)
            )
            return result.rowcount == 1

    async def lock_lease(
        self, db: AsyncSession, lease: JobLease
    ) -> IngestionJob | None:
        """Hold this row through vector/content publication and terminal-state commit."""
        return (
            await db.execute(
                select(IngestionJob)
                .where(*self.lease_predicate(lease))
                .with_for_update()
            )
        ).scalar_one_or_none()

    async def fail(self, lease: JobLease, code: str, *, retryable: bool) -> bool:
        if code not in {
            "storage_unavailable",
            "invalid_document",
            "empty_document",
            "provider_unavailable",
            "provider_quota",
            "provider_not_approved",
            "provider_rate_limit",
            "provider_invalid_response",
            "document_removed",
            "processing_failed",
        }:
            raise ValueError("Failure reason must be a server-owned code")
        async with self.sessions() as db, db.begin():
            job = await self.lock_lease(db, lease)
            if not job:
                return False
            job.status = (
                "RETRY" if retryable and job.attempts < MAX_ATTEMPTS else "FAILED"
            )
            job.stage = "retry_wait" if job.status == "RETRY" else "failed"
            job.error_code = code
            job.available_at = datetime.now(UTC) + timedelta(
                seconds=min(5 * 2**job.attempts, 60)
            )
            job.lease_token = job.lease_expires_at = None
            await self.mirror_document_state(db, job)
            return True

    @staticmethod
    async def mirror_document_state(db, job):
        from src.models.document import Document
        from src.models.external_document import ExternalDocument

        model = Document if job.document_kind == "standalone" else ExternalDocument
        row = await db.get(model, job.document_id)
        if row is None:
            return
        # A failed replacement must not hide the previous working index. Job progress
        # reports the replacement failure independently of the active document state.
        working = bool(row.chunk_count) and row.status in {"indexed", "READY"}
        if not working:
            states = {
                "standalone": {"RUNNING": "processing", "QUEUED": "queued", "RETRY": "queued", "FAILED": f"failed:{job.error_code or 'processing_failed'}"},
                "external": {"RUNNING": "PROCESSING", "QUEUED": "QUEUED", "RETRY": "QUEUED", "FAILED": "FAILED"},
            }
            row.status = states[job.document_kind].get(job.status, row.status)
        if job.document_kind == "external":
            row.processing_error = job.error_code


ingestion_queue = IngestionQueue()
