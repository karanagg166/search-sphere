"""Dry-run-first, exact-scope adoption of published sources without usable vectors."""

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import aliased

from src.config import settings
from src.inference.cohere_gateway import EMBEDDING_MODEL
from src.inference.policy import (
    ProviderNotApprovedError,
    authorize_cohere,
    inference_scope,
)
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.ingestion_job import IngestionJob
from src.repositories.document_deletion import find_deletion, lock_document_identity
from src.repositories.ingestion_queue import IngestionQueue
from src.retrieval.permission_cache import invalidate_scope
from src.security.query_access import QueryAccess
from src.security.service_context import ServiceContext
from src.services.ingestion_source import load_ingestion_source, reauthorize_ingestion

MAX_BATCH = 20
PLAN_TTL = 900
PENDING = {"QUEUED", "RETRY", "RUNNING"}


@dataclass(frozen=True)
class RecoveryScope:
    kind: str
    client_id: str
    tenant_id: str
    collection_id: str | None = None
    subject_id: str | None = None

    def __post_init__(self):
        if self.kind not in {"standalone", "external"}:
            raise ValueError("Invalid recovery source kind.")
        for value, maximum in ((self.client_id, 64), (self.tenant_id, 128), (self.collection_id, 128), (self.subject_id, 128)):
            if value is not None and (not isinstance(value, str) or len(value) > maximum or not re.fullmatch(r"[A-Za-z0-9_.:-]+", value)):
                raise ValueError("Invalid exact recovery scope.")
        if not self.client_id or not self.tenant_id:
            raise ValueError("Client and tenant are required.")
        if self.kind == "standalone" and (self.client_id != "search_sphere" or self.subject_id != self.tenant_id or self.collection_id is not None):
            raise ValueError("Native recovery must bind exactly its owner.")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def parent_model(scope):
    return Document if scope.kind == "standalone" else ExternalDocument


def predicates(scope, model):
    if scope.kind == "standalone":
        return [model.user_id == scope.tenant_id]
    return [model.source_system == scope.client_id, model.tenant_id == scope.tenant_id,
            model.collection_id.is_not_distinct_from(scope.collection_id),
            model.owner_subject_id.is_not_distinct_from(scope.subject_id)]


def access_for(sessions, scope):
    context = ServiceContext(scope.client_id, scope.tenant_id, subject_id=scope.subject_id,
        collection_id=scope.collection_id, user_id=scope.tenant_id if scope.kind == "standalone" else None,
        is_service_client=scope.kind == "external")
    return QueryAccess(sessions, context, "documents:write")


def usable_query(sessions, scope, identity):
    return access_for(sessions, scope).chunk_query().where(
        DocumentChunk.metadata_json["source_record_id"].as_string() == identity)


async def assess(db, sessions, scope, row, job):
    if row.status != ("indexed" if scope.kind == "standalone" else "READY"):
        return "state_changed", None
    document_id = row.id if scope.kind == "standalone" else row.external_document_id
    if await find_deletion(db, scope.kind, scope.client_id, scope.tenant_id, document_id):
        return "deleted", None
    if job is not None and job.status in PENDING:
        return "pending_job", None
    count = await db.scalar(select(func.count()).select_from(usable_query(sessions, scope, row.id).subquery()))
    if count:
        return "working_index", None
    try:
        source = await load_ingestion_source(db, scope.kind, row.id)
        async def validate():
            await reauthorize_ingestion(sessions, source)
        with inference_scope(source.classification, source.client_id, source.tenant_id, validator=validate):
            authorize_cohere(settings.COHERE_API_KEY or "")  # Eligibility only: no quota or HTTP call.
    except ProviderNotApprovedError:
        return "provider_not_approved", None
    snapshot = {"source": asdict(source), "status": row.status, "updated_at": row.updated_at,
                "chunk_count": row.chunk_count, "job": None if job is None else
                [job.id, job.status, job.generation, job.attempts, job.updated_at]}
    return "enqueue", digest(snapshot)


async def build_recovery_plan(sessions, scope, *, limit=MAX_BATCH, after_id=None):
    if type(limit) is not int or not 1 <= limit <= MAX_BATCH:
        raise ValueError("Recovery batch must contain 1 to 20 sources.")
    if after_id is not None and (not isinstance(after_id, str) or not re.fullmatch(r"[0-9a-f-]{36}", after_id)):
        raise ValueError("Invalid recovery cursor.")
    outer = aliased(parent_model(scope))
    query = select(outer).where(*predicates(scope, outer),
        outer.status == ("indexed" if scope.kind == "standalone" else "READY"),
        ~usable_query(sessions, scope, outer.id).exists())
    if after_id:
        query = query.where(outer.id > after_id)
    async with sessions() as db, db.begin():
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await db.execute(query.order_by(outer.id).limit(limit))).scalars().all()
        entries = []
        for row in rows:
            job = (await db.execute(select(IngestionJob).where(
                IngestionJob.document_kind == scope.kind, IngestionJob.document_id == row.id))).scalar_one_or_none()
            action, fingerprint = await assess(db, sessions, scope, row, job)
            entries.append({"id": row.id, "action": action, "fingerprint": fingerprint})
    return {"version": 1, "created_at": int(time.time()), "embedding_model": EMBEDDING_MODEL,
            "scope": asdict(scope), "entries": entries,
            "next_after_id": rows[-1].id if len(rows) == limit else None}


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != {"version", "created_at", "embedding_model", "scope", "entries", "next_after_id"}:
        raise ValueError("Invalid recovery receipt.")
    if plan["version"] != 1 or plan["embedding_model"] != EMBEDDING_MODEL:
        raise ValueError("Recovery receipt version/model changed.")
    if type(plan["created_at"]) is not int or not 0 <= time.time() - plan["created_at"] <= PLAN_TTL:
        raise ValueError("Recovery receipt expired or has an invalid timestamp.")
    scope = RecoveryScope(**plan["scope"])
    rows = plan["entries"]
    if not isinstance(rows, list) or len(rows) > MAX_BATCH:
        raise ValueError("Recovery receipt exceeds the batch bound.")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "action", "fingerprint"}
            or not isinstance(row["id"], str) or not re.fullmatch(r"[0-9a-f-]{36}", row["id"]) or row["id"] in seen
            or row["action"] not in {"enqueue", "state_changed", "deleted", "pending_job", "working_index", "provider_not_approved"}
            or (row["action"] == "enqueue" and (not isinstance(row["fingerprint"], str) or not re.fullmatch(r"[0-9a-f]{64}", row["fingerprint"])))):
            raise ValueError("Invalid or repeated recovery identity.")
        seen.add(row["id"])
    return scope


async def apply_recovery_plan(sessions, plan, expected_digest):
    scope = validate_plan(plan)
    if not isinstance(expected_digest, str) or digest(plan) != expected_digest:
        raise ValueError("Reviewed recovery receipt digest does not match.")
    queue = IngestionQueue(sessions)
    results = []
    async with sessions() as db, db.begin():
        for entry in sorted(plan["entries"], key=lambda value: value["id"]):
            if entry["action"] != "enqueue":
                results.append({"id": entry["id"], "result": "not_eligible_in_plan"})
                continue
            model = parent_model(scope)
            query = select(model).where(model.id == entry["id"], *predicates(scope, model))
            row = (await db.execute(query)).scalar_one_or_none()
            if row is None:
                results.append({"id": entry["id"], "result": "source_unavailable"})
                continue
            document_id = row.id if scope.kind == "standalone" else row.external_document_id
            await lock_document_identity(db, scope.kind, scope.client_id, scope.tenant_id, document_id)
            job = (await db.execute(select(IngestionJob).where(IngestionJob.document_kind == scope.kind,
                IngestionJob.document_id == row.id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            row = (await db.execute(query.with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
            if row is None:
                results.append({"id": entry["id"], "result": "source_unavailable"})
                continue
            action, fingerprint = await assess(db, sessions, scope, row, job)
            if action != "enqueue" or fingerprint != entry["fingerprint"]:
                results.append({"id": entry["id"], "result": action if action != "enqueue" else "snapshot_changed"})
                continue
            job = await queue.enqueue(db, scope.kind, row.id, reset=True)
            job.stage = "recovering"
            row.chunk_count = 0  # The scoped count proved the old published index unusable.
            await queue.mirror_document_state(db, job)
            await invalidate_scope(db, scope.client_id, scope.tenant_id)
            results.append({"id": row.id, "result": "enqueued"})
    return results
