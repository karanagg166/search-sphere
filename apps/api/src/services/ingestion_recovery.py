"""Bounded adoption of pre-outbox pending work; never steals a live legacy lease."""

import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, or_, select

from src.models.document import Document
from src.models.external_document import ExternalDocument
from src.models.ingestion_job import IngestionJob


def legacy_lease_active(row):
    if row.status.startswith("proc:"):
        try:
            return int(row.status.split(":")[2]) > time.time()
        except (ValueError, IndexError):
            pass
    if row.status.startswith(("processing", "proc:", "PROCESSING")):
        updated = row.updated_at if row.updated_at.tzinfo else row.updated_at.replace(tzinfo=UTC)
        return updated > datetime.now(UTC) - timedelta(minutes=5)
    return False


async def adopt_legacy_pending(sessions, queue, limit=20, *, owner_user_id=None):
    adopted = 0
    for kind, model in (("standalone", Document), ("external", ExternalDocument)):
        if owner_user_id is not None and kind != "standalone":
            continue
        async with sessions() as db, db.begin():
            pending = or_(model.status == "uploaded", model.status.like("uploaded:retry:%"), model.status.like("proc:%"), model.status.like("processing%")) if kind == "standalone" else model.status.in_(("QUEUED", "PROCESSING"))
            query = select(model).where(
                pending,
                ~exists(select(IngestionJob.id).where(IngestionJob.document_kind == kind, IngestionJob.document_id == model.id)),
            )
            if owner_user_id is not None:
                query = query.where(Document.user_id == owner_user_id)
            rows = (await db.execute(query.order_by(model.created_at).limit(limit).with_for_update(skip_locked=True))).scalars().all()
            for row in rows:
                if legacy_lease_active(row):
                    continue
                job = await queue.enqueue(db, kind, row.id)
                job.queued_at = row.created_at
                job.stage = "recovering"
                row.status = "queued" if kind == "standalone" else "QUEUED"
                adopted += 1
    return adopted
