"""Resolve and reauthorize ingestion resources from PostgreSQL metadata."""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from src.config import settings
from src.inference.data_policy import classification_for_scope
from src.inference.policy import DataClassification, ProviderNotApprovedError
from src.models.document import Document
from src.models.external_document import ExternalDocument
from src.models.service_client import ServiceClient
from src.models.user import User


class IngestionSourceError(RuntimeError):
    def __init__(self, code, *, retryable=False):
        super().__init__(code)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class IngestionSource:
    kind: str
    internal_id: str
    document_id: str
    storage_key: str = field(repr=False)
    mime_type: str
    file_size: int
    client_id: str
    tenant_id: str
    collection_id: str | None
    subject_id: str | None
    classification: DataClassification
    file_name: str = field(repr=False)
    document_type: str | None = None
    report_date: datetime | None = None

    def vector_payload(self):
        from src.inference.cohere_gateway import EMBEDDING_MODEL

        metadata = {"embedding_model": EMBEDDING_MODEL, "source_record_id": self.internal_id}
        if self.kind == "standalone":
            return metadata
        return {
            **metadata, "client_id": self.client_id, "tenant_id": self.tenant_id,
            "collection_id": self.collection_id, "owner_subject_id": self.subject_id,
            "patient_id": self.subject_id, "source_system": self.client_id,
            "file_name": self.file_name, "document_type": self.document_type,
            "report_date": self.report_date.isoformat() if self.report_date else None,
        }


async def load_ingestion_source(db, kind, document_id):
    model = Document if kind == "standalone" else ExternalDocument
    row = await db.get(model, document_id)
    if row is None:
        raise IngestionSourceError("document_removed")
    if kind == "standalone":
        owner = await db.get(User, row.user_id)
        if owner is None or not owner.is_active:
            raise ProviderNotApprovedError("Document owner is inactive.")
        client_id, tenant_id, collection_id, subject_id = "search_sphere", row.user_id, None, row.user_id
    else:
        client_id, tenant_id = row.client_id, row.tenant_id
        collection_id, subject_id = row.collection_id, row.owner_subject_id
        client = (await db.execute(select(ServiceClient).where(ServiceClient.client_id == client_id))).scalar_one_or_none()
        if client:
            allowed = client.is_active() and client.is_tenant_authorized(tenant_id) and client.has_scope("documents:write")
        else:
            allowed = client_id == "quick_clinic" and tenant_id == "quick_clinic_default" and bool(settings.QUICK_CLINIC_SERVICE_SECRET)
        if not allowed:
            raise ProviderNotApprovedError("Delegated client is no longer authorized for ingestion.")
    classification = await classification_for_scope(db, client_id, tenant_id, collection_id, subject_id)
    return IngestionSource(
        kind=kind, internal_id=row.id,
        document_id=row.id if kind == "standalone" else row.external_document_id,
        storage_key=row.storage_key if kind == "standalone" else row.storage_path,
        mime_type=row.mime_type, file_size=row.file_size,
        client_id=client_id, tenant_id=tenant_id, collection_id=collection_id, subject_id=subject_id,
        classification=classification,
        file_name=row.filename if kind == "standalone" else row.file_name,
        document_type=None if kind == "standalone" else row.document_type,
        report_date=None if kind == "standalone" else row.report_date,
    )


async def reauthorize_ingestion(sessions, source):
    async with sessions() as db:
        current = await load_ingestion_source(db, source.kind, source.internal_id)
    if current != source:
        raise ProviderNotApprovedError("Document scope, content or data policy changed during ingestion.")
