"""Load job metadata only after the parent documents have been authorized."""

from datetime import UTC, datetime

from sqlalchemy import select

from src.models.ingestion_job import IngestionJob
from src.schemas.ingestion import IngestionProgress


async def attach_job_progress(db, kind, documents):
    if not documents:
        return
    ids = [document.id for document in documents]
    jobs = (await db.execute(select(IngestionJob).where(
        IngestionJob.document_kind == kind, IngestionJob.document_id.in_(ids),
    ))).scalars().all()
    by_id = {job.document_id: job for job in jobs}
    now = datetime.now(UTC)
    for document in documents:
        job = by_id.get(document.id)
        document.job = None
        if job:
            queued_at = job.queued_at if job.queued_at.tzinfo else job.queued_at.replace(tzinfo=UTC)
            ended_at = job.updated_at if job.updated_at.tzinfo else job.updated_at.replace(tzinfo=UTC)
            observed_at = ended_at if job.status in {"SUCCEEDED", "FAILED"} else now
            document.job = IngestionProgress(
                status=job.status, stage=job.stage, attempts=job.attempts, generation=job.generation,
                queue_age_seconds=round(max(0, (observed_at - queued_at).total_seconds()), 2),
                queued_at=queued_at, updated_at=job.updated_at, error_code=job.error_code,
            )
