"""Fresh PostgreSQL authorization before any query content reaches hosted inference."""

import hashlib
import hmac
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import settings
from src.db import get_db
from src.inference.cohere_gateway import EMBEDDING_MODEL
from src.inference.data_policy import (
    classification_for_scope,
    classifications_for_scopes,
)
from src.inference.policy import ProviderNotApprovedError, inference_scope
from src.models.conversation import Conversation
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.service_client import ServiceClient
from src.models.user import User
from src.security.jwt import get_current_user
from src.security.medical_context import bind_patient, get_medical_context
from src.security.service_auth import (
    LEGACY_MEDICAL_SCOPES,
    _authenticate_service_context,
    hash_api_key,
)
from src.security.service_context import ServiceContext

_access: ContextVar["QueryAccess | None"] = ContextVar("query_access", default=None)


def current_query_access():
    return _access.get()


def scope_value(body, key, maximum=128):
    value = body.get(key) if isinstance(body, dict) else None
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > maximum):
        raise HTTPException(status_code=422, detail="Invalid query scope identifier.")
    return value.strip() if isinstance(value, str) else None


@dataclass
class QueryAccess:
    sessions: object
    context: ServiceContext
    operation: str
    key_hash: str | None = field(default=None, repr=False)
    document_id: str | None = None
    conversation_id: str | None = None
    classification: object = None
    evidence: dict[str, str] = field(default_factory=dict, repr=False)

    async def validate(self):
        async with self.sessions() as db:
            context = self.context
            if context.is_service_client:
                client = (await db.execute(select(ServiceClient).where(ServiceClient.client_id == context.client_id))).scalar_one_or_none()
                legacy = context.client_id == "quick_clinic" and context.tenant_id == "quick_clinic_default" and hmac.compare_digest(
                    self.key_hash or "", hash_api_key(settings.QUICK_CLINIC_SERVICE_SECRET or ""),
                ) and bool(settings.QUICK_CLINIC_SERVICE_SECRET)
                if client is None:
                    allowed = legacy and self.operation in LEGACY_MEDICAL_SCOPES
                else:
                    allowed = client.is_active() and client.is_tenant_authorized(context.tenant_id) and client.has_scope(self.operation)
                    allowed = allowed and (legacy or hmac.compare_digest(client.api_key_hash, self.key_hash or ""))
                if not allowed:
                    raise ProviderNotApprovedError("Delegated query authorization was revoked.")
            else:
                user = await db.get(User, context.user_id)
                if user is None or not user.is_active:
                    raise ProviderNotApprovedError("Query account authorization was revoked.")
                if self.document_id and (await db.execute(select(Document.id).where(
                    Document.id == self.document_id, Document.user_id == context.user_id,
                ))).scalar_one_or_none() is None:
                    raise ProviderNotApprovedError("Selected document access was revoked.")
                if self.conversation_id and (await db.execute(select(Conversation.id).where(
                    Conversation.id == self.conversation_id, Conversation.user_id == context.user_id,
                ))).scalar_one_or_none() is None:
                    raise ProviderNotApprovedError("Conversation access was revoked.")
            rows = []
            if self.evidence:
                rows = (await db.execute(self.chunk_query().where(DocumentChunk.point_id.in_(list(self.evidence))))).scalars().all()
                if len(rows) != len(self.evidence) or any(hashlib.sha256(row.content.encode()).hexdigest() != self.evidence.get(row.point_id) for row in rows):
                    raise ProviderNotApprovedError("Source evidence access or version changed.")
            requested_scope = (context.collection_id or "", context.subject_id or "")
            scopes = {requested_scope}
            if context.is_service_client:
                scopes.update((row.collection_id or "", row.owner_subject_id or "") for row in rows)
            policies = await classifications_for_scopes(db, context.client_id, context.tenant_id, scopes)
            if self.classification is not None and any(value != self.classification for value in policies.values()):
                raise ProviderNotApprovedError("Query or evidence provider eligibility changed.")

    def chunk_query(self):
        context = self.context
        query = select(DocumentChunk).where(DocumentChunk.metadata_json["embedding_model"].as_string() == EMBEDDING_MODEL)
        if context.is_service_client:
            query = query.join(ExternalDocument, (
                (ExternalDocument.id == DocumentChunk.metadata_json["source_record_id"].as_string())
                & (ExternalDocument.external_document_id == DocumentChunk.document_id)
                & (ExternalDocument.source_system == DocumentChunk.client_id)
                & (ExternalDocument.tenant_id == DocumentChunk.tenant_id)
                & ExternalDocument.collection_id.is_not_distinct_from(DocumentChunk.collection_id)
                & ExternalDocument.owner_subject_id.is_not_distinct_from(DocumentChunk.owner_subject_id)
            )).where(
                ExternalDocument.source_system == context.client_id, ExternalDocument.tenant_id == context.tenant_id,
                ExternalDocument.status == "READY",
            )
            if context.subject_id:
                query = query.where(ExternalDocument.owner_subject_id == context.subject_id, DocumentChunk.owner_subject_id == context.subject_id)
            if context.collection_id:
                query = query.where(ExternalDocument.collection_id == context.collection_id, DocumentChunk.collection_id == context.collection_id)
        else:
            query = query.join(Document, Document.id == DocumentChunk.document_id).where(
                Document.user_id == context.user_id, Document.status == "indexed",
                DocumentChunk.client_id.is_(None), DocumentChunk.source_system.is_(None),
                DocumentChunk.metadata_json["source_record_id"].as_string() == Document.id,
            )
            if self.document_id:
                query = query.where(Document.id == self.document_id)
        return query

    def sql_filters(self):
        context = self.context
        params = {"auth_embedding_model": EMBEDDING_MODEL, "auth_tenant": context.tenant_id}
        clauses = ["metadata_json->>'embedding_model' = :auth_embedding_model"]
        if context.is_service_client:
            params["auth_client"] = context.client_id
            inner = [
                "ed.id = document_chunks.metadata_json->>'source_record_id'",
                "ed.external_document_id = document_chunks.document_id", "ed.source_system = document_chunks.client_id",
                "ed.tenant_id = document_chunks.tenant_id", "ed.status = 'READY'",
                "ed.collection_id IS NOT DISTINCT FROM document_chunks.collection_id",
                "ed.owner_subject_id IS NOT DISTINCT FROM document_chunks.owner_subject_id",
                "ed.source_system = :auth_client", "ed.tenant_id = :auth_tenant",
            ]
            if context.subject_id:
                params["auth_subject"] = context.subject_id
                inner += ["ed.owner_subject_id = :auth_subject", "document_chunks.owner_subject_id = :auth_subject"]
            if context.collection_id:
                params["auth_collection"] = context.collection_id
                inner += ["ed.collection_id = :auth_collection", "document_chunks.collection_id = :auth_collection"]
            clauses.append("EXISTS (SELECT 1 FROM external_documents ed WHERE " + " AND ".join(inner) + ")")
        else:
            clauses += ["client_id IS NULL", "source_system IS NULL", "EXISTS (SELECT 1 FROM documents d WHERE d.id = document_chunks.document_id AND d.id = document_chunks.metadata_json->>'source_record_id' AND d.user_id = :auth_tenant AND d.status = 'indexed')"]
            if self.document_id:
                params["auth_document"] = self.document_id
                clauses.append("document_id = :auth_document")
        return clauses, params

    async def authorize_candidates(self, candidates):
        await self.validate()
        identifiers = list({candidate.point_id for candidate in candidates})
        async with self.sessions() as db:
            rows = (await db.execute(self.chunk_query().where(DocumentChunk.point_id.in_(identifiers)))).scalars().all()
        by_id = {row.point_id: row for row in rows}
        authorized = []
        for candidate in candidates:
            row = by_id.get(candidate.point_id)
            if row is None:
                continue
            self.evidence[row.point_id] = hashlib.sha256(row.content.encode()).hexdigest()
            authorized.append(replace(candidate, content=row.content, document_id=row.document_id, chunk_index=row.chunk_index,
                token_count=row.token_count, start_page=row.start_page, end_page=row.end_page,
                page_numbers=row.page_numbers, block_types=row.block_types, client_id=row.client_id, tenant_id=row.tenant_id,
                collection_id=row.collection_id, owner_subject_id=row.owner_subject_id, patient_id=row.patient_id,
                source_system=row.source_system, file_name=row.file_name, document_type=row.document_type))
        await self.validate()
        return authorized


@asynccontextmanager
async def query_inference(access):
    async with access.sessions() as db:
        context = access.context
        access.classification = await classification_for_scope(db, context.client_id, context.tenant_id, context.collection_id, context.subject_id)
    token = _access.set(access)
    try:
        with inference_scope(access.classification, access.context.client_id, access.context.tenant_id, validator=access.validate):
            yield access
    finally:
        _access.reset(token)


async def native_query_inference(request: Request, user=Depends(get_current_user), db=Depends(get_db)):
    body = await request.json()
    document_id = scope_value(body, "document_id")
    conversation_id = request.path_params.get("conversation_id")
    if document_id and (await db.execute(select(Document.id).where(Document.id == document_id, Document.user_id == user.id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    if conversation_id and (await db.execute(select(Conversation.id).where(Conversation.id == conversation_id, Conversation.user_id == user.id))).scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    access = QueryAccess(async_sessionmaker(db.bind, expire_on_commit=False),
        ServiceContext("search_sphere", user.id, subject_id=user.id, user_id=user.id, is_service_client=False),
        "answers:generate" if "answer" in request.url.path else "search:execute", document_id=document_id, conversation_id=conversation_id)
    async with query_inference(access):
        yield


async def delegated_query_inference(request: Request, context=Depends(_authenticate_service_context), db=Depends(get_db)):
    body = await request.json()
    if isinstance(body, dict):
        context = context.resolve_scope(scope_value(body, "collection_id"), scope_value(body, "owner_subject_id", 255))
    context.require_scope("answers:generate" if "answers" in request.url.path else "search:execute")
    access = QueryAccess(async_sessionmaker(db.bind, expire_on_commit=False), context,
        "answers:generate" if "answers" in request.url.path else "search:execute",
        key_hash=hash_api_key(request.headers.get("Authorization", "")[7:].strip()))
    async with query_inference(access):
        yield


async def medical_query_inference(request: Request, context=Depends(get_medical_context), db=Depends(get_db)):
    body = await request.json()
    if isinstance(body, dict):
        patient = scope_value(body, "patientId") or scope_value(body, "patient_id")
        if isinstance(patient, str):
            context = bind_patient(context, patient)
    operation = "answers:generate" if "medical-rag" in request.url.path else "search:execute"
    context.require_scope(operation)
    access = QueryAccess(async_sessionmaker(db.bind, expire_on_commit=False), context, operation,
        key_hash=hash_api_key(request.headers.get("Authorization", "")[7:].strip()))
    async with query_inference(access):
        yield
