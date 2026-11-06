"""Cache only signed source references; SQL authorization remains authoritative."""

import hashlib
import hmac
import json
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert

from src.config import settings
from src.inference.cohere_gateway import EMBEDDING_MODEL, RERANK_MODEL
from src.inference.policy import ProviderNotApprovedError, authorize_cohere
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.retrieval_cache import RetrievalCache
from src.processing.models.search import RerankedSearchResult

MAX_ENTRIES = 128
MAX_SCOPE_CHUNKS = 10000
TTL_SECONDS = 900


def _signed(value):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), encoded, hashlib.sha256).hexdigest()


async def invalidate_scope(db, client_id, tenant_id):
    """Call in the document publication/deletion transaction, including empty hits."""
    await db.execute(delete(RetrievalCache).where(
        RetrievalCache.client_id == client_id, RetrievalCache.tenant_id == tenant_id))


class PermissionRetrievalCache:
    def __init__(self, access, key, manifest):
        self.access, self.key, self.manifest = access, key, manifest

    @staticmethod
    async def _manifest(access):
        model = ExternalDocument if access.context.is_service_client else Document
        query = access.chunk_query().with_only_columns(
            DocumentChunk.id, DocumentChunk.point_id, model.id, model.updated_at,
            DocumentChunk.metadata_json["version_id"].as_string(),
        ).order_by(DocumentChunk.point_id).limit(MAX_SCOPE_CHUNKS + 1)
        async with access.sessions() as db:
            rows = (await db.execute(query)).all()
        if len(rows) > MAX_SCOPE_CHUNKS:
            return None
        return _signed([[str(value) if value is not None else None for value in row] for row in rows])

    @classmethod
    async def prepare(cls, access, parameters):
        if access is None or not settings.JWT_SECRET_KEY:
            return None
        context = access.context
        actor = context.actor_id if context.is_service_client else context.user_id
        if not actor or access.sessions.kw["bind"].dialect.name != "postgresql":
            return None
        await access.validate()
        try:
            authorize_cohere(settings.COHERE_API_KEY)  # Hits cannot bypass provider eligibility.
        except ProviderNotApprovedError:
            return None
        manifest = await cls._manifest(access)
        if manifest is None:
            return None
        key = _signed({"schema": 1, "client": context.client_id, "tenant": context.tenant_id, "actor": actor,
            "native": not context.is_service_client, "collection": context.collection_id, "subject": context.subject_id,
            "operation": access.operation, "scopes": sorted(context.scopes), "document": access.document_id,
            "conversation": access.conversation_id, "history": access.history_hashes,
            "requires_history": access.requires_history, "selected_messages": access.selected_message_ids,
            "classification": access.classification, "policy": access.routing_policy_hash, "manifest": manifest,
            "embedding": EMBEDDING_MODEL, "rerank": RERANK_MODEL, "parameters": parameters,
            "eligibility": [settings.COHERE_TRIAL_KEY_SHA256, settings.COHERE_APPROVED_DATA_CLASSIFICATIONS,
                settings.COHERE_SENSITIVE_APPROVAL_REF]})
        return cls(access, key, manifest)

    async def _discard(self):
        async with self.access.sessions() as db, db.begin():
            await db.execute(delete(RetrievalCache).where(RetrievalCache.key == self.key))

    async def get(self):
        await self.access.validate()
        authorize_cohere(settings.COHERE_API_KEY)
        async with self.access.sessions() as db:
            cached = await db.get(RetrievalCache, self.key)
        if cached is None:
            return None
        if cached.expires_at <= datetime.now(UTC) or not hmac.compare_digest(cached.signature, _signed([self.key, cached.entries])):
            await self._discard()
            return None
        identifiers = [entry["point_id"] for entry in cached.entries]
        async with self.access.sessions() as db:
            rows = (await db.execute(self.access.chunk_query().where(DocumentChunk.point_id.in_(identifiers)))).scalars().all()
        by_id = {row.point_id: row for row in rows}
        results = []
        for index, entry in enumerate(cached.entries):
            row = by_id.get(entry["point_id"])
            if row is None or str(row.id) != entry["row_id"] or hashlib.sha256(row.content.encode()).hexdigest() != entry["sha256"]:
                await self._discard()
                return None
            results.append(RerankedSearchResult(point_id=row.point_id, document_id=row.document_id, chunk_index=row.chunk_index,
                content=row.content, token_count=row.token_count, start_page=row.start_page, end_page=row.end_page,
                page_numbers=row.page_numbers, block_types=row.block_types, rerank_score=entry["rerank_score"],
                rrf_score=entry["rrf_score"], rank=index + 1))
        if await self._manifest(self.access) != self.manifest:
            await self._discard()
            return None
        return await self.access.authorize_candidates(results)

    async def put(self, results):
        if len(results) > 20:
            return
        await self.access.validate()
        authorize_cohere(settings.COHERE_API_KEY)
        if await self._manifest(self.access) != self.manifest:
            return
        async with self.access.sessions() as db:
            rows = (await db.execute(self.access.chunk_query().where(
                DocumentChunk.point_id.in_([result.point_id for result in results])))).scalars().all()
        by_id = {row.point_id: row for row in rows}
        entries = []
        for result in results:
            row = by_id.get(result.point_id)
            if row is None or row.content != result.content or not math.isfinite(result.rerank_score) or (
                result.rrf_score is not None and not math.isfinite(result.rrf_score)):
                return
            entries.append({"point_id": row.point_id, "row_id": str(row.id),
                "sha256": hashlib.sha256(row.content.encode()).hexdigest(),
                "rerank_score": result.rerank_score, "rrf_score": result.rrf_score})
        if len(json.dumps(entries).encode()) > 16000:
            return
        context = self.access.context
        now = datetime.now(UTC)
        async with self.access.sessions() as db, db.begin():
            # A global 128-entry bound protects free-tier storage under concurrent requests.
            await db.execute(text("SELECT pg_advisory_xact_lock(773642020)"))
            await db.execute(delete(RetrievalCache).where(RetrievalCache.expires_at <= now))
            await db.execute(insert(RetrievalCache).values(key=self.key, client_id=context.client_id, tenant_id=context.tenant_id,
                actor_id=context.actor_id if context.is_service_client else context.user_id,
                entries=entries, signature=_signed([self.key, entries]), expires_at=now + timedelta(seconds=TTL_SECONDS)
            ).on_conflict_do_update(index_elements=[RetrievalCache.key], set_={"entries": entries,
                "signature": _signed([self.key, entries]), "expires_at": now + timedelta(seconds=TTL_SECONDS)}))
            excess = select(RetrievalCache.key).order_by(RetrievalCache.created_at.desc(), RetrievalCache.key).offset(MAX_ENTRIES)
            await db.execute(delete(RetrievalCache).where(RetrievalCache.key.in_(excess)))
