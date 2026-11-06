"""Real PostgreSQL processing and publication; provider HTTP is synthetic and isolated."""

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pymupdf
import pytest_asyncio
from fastapi import UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.config import settings
from src.inference.cohere_gateway import CohereGateway
from src.models.conversation import Conversation
from src.models.document import Document
from src.models.document_chunk import DocumentChunk
from src.models.document_deletion import DocumentDeletion
from src.models.document_evidence import DocumentEvidence
from src.models.external_document import ExternalDocument
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.ingestion_job import IngestionJob
from src.models.medical_observation import MedicalObservation
from src.models.service_client import ServiceClient
from src.models.user import User
from src.processing.chunking.semantic_splitter import SemanticEmbedder
from src.processing.embedding.dense_embedder import DenseEmbedder
from src.services.document_service import DocumentService
from src.storage.object_storage import LocalStorage


@pytest_asyncio.fixture
async def fixture(tmp_path, monkeypatch):
    url = os.environ.get("INGESTION_TEST_DATABASE_URL", "")
    parsed = urlsplit(url)
    assert url and parsed.hostname in {"localhost", "127.0.0.1", "host.docker.internal", "postgres"}
    assert parsed.path in {"/ss_test", "/search_sphere_test"}
    engine = create_async_engine(url, hide_parameters=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = str(uuid.uuid4())
    key = "synthetic-ingestion-key-" + owner_id
    monkeypatch.setattr(settings, "COHERE_API_KEY", key)
    monkeypatch.setattr(settings, "COHERE_TRIAL_KEY_SHA256", hashlib.sha256(key.encode()).hexdigest())
    DenseEmbedder._clear_cache()
    SemanticEmbedder._clear_cache()
    calls = []

    async def public_provider(self, endpoint, payload):
        calls.append((endpoint, payload["model"]))
        if endpoint == "/v1/tokenize":
            return {"tokens": list(range(max(1, len(payload["text"].split()))))}
        assert endpoint == "/v2/embed"  # This fixture never invokes a real provider.
        return {"embeddings": {"float": [[1.0] + [0.0] * 383 for _ in payload["texts"]]}}

    monkeypatch.setattr(CohereGateway, "_request", public_provider)
    monkeypatch.setattr("src.inference.cohere_gateway.CohereUsageBudget.reserve", AsyncMock())
    monkeypatch.setattr("src.inference.cohere_gateway.CohereUsageBudget.wait_for_tokenize_slot", AsyncMock())
    storage = LocalStorage(str(tmp_path), "synthetic-fixtures")
    async with sessions() as db, db.begin():
        await db.execute(delete(IngestionJob))  # Dedicated synthetic test DB only.
        db.add(User(id=owner_id, email=f"{owner_id}@example.invalid", name="Synthetic owner"))
    value = SimpleNamespace(sessions=sessions, owner_id=owner_id, storage=storage, calls=calls, external_ids=[], client_ids=[], observation_ids=[])
    try:
        yield value
    finally:
        async with sessions() as db, db.begin():
            await db.execute(delete(Conversation).where((Conversation.client_id == owner_id) | (Conversation.external_reference == owner_id)))
            native_ids = select(Document.id).where(Document.user_id == owner_id)
            await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(native_ids)))
            await db.execute(delete(DocumentChunk).where(DocumentChunk.client_id == owner_id))
            if value.external_ids:
                await db.execute(delete(DocumentChunk).where(DocumentChunk.metadata_json["source_record_id"].as_string().in_(value.external_ids)))
                await db.execute(delete(ExternalDocument).where(ExternalDocument.id.in_(value.external_ids)))
            await db.execute(delete(MedicalObservation).where(MedicalObservation.external_patient_id == owner_id))
            if value.observation_ids:
                await db.execute(delete(MedicalObservation).where(MedicalObservation.id.in_(value.observation_ids)))
            await db.execute(delete(ExternalDocument).where(ExternalDocument.source_system == owner_id))
            await db.execute(delete(IngestionJob))
            await db.execute(delete(DocumentEvidence).where((DocumentEvidence.tenant_id == owner_id) | (DocumentEvidence.client_id == owner_id) | (DocumentEvidence.subject_id == owner_id)))
            await db.execute(delete(DocumentDeletion).where((DocumentDeletion.tenant_id == owner_id) | (DocumentDeletion.client_id == owner_id) | (DocumentDeletion.subject_id == owner_id)))
            await db.execute(delete(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == owner_id))
            await db.execute(delete(InferenceDataPolicy).where(InferenceDataPolicy.client_id == owner_id))
            await db.execute(delete(InferenceDataPolicy).where(InferenceDataPolicy.subject_scope == owner_id))
            await db.execute(delete(ServiceClient).where(ServiceClient.client_id == owner_id))
            if value.client_ids:
                await db.execute(delete(ServiceClient).where(ServiceClient.id.in_(value.client_ids)))
            await db.execute(delete(User).where(User.id == owner_id))
        await engine.dispose()


async def approve(fixture, client="search_sphere", tenant=None, subject=None, collection=""):
    async with fixture.sessions() as db, db.begin():
        db.add(InferenceDataPolicy(
            client_id=client, tenant_id=tenant or fixture.owner_id,
            collection_scope=collection, subject_scope=subject or "",
            classification="synthetic", approval_ref="public-synthetic-test-fixture",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))


def pdf(text="PUBLIC SYNTHETIC MATERNITY POLICY. Interns are covered. SS-QUEUE-42."):
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


async def upload_native(fixture):
    async with fixture.sessions() as db:
        user = await db.get(User, fixture.owner_id)
        return await DocumentService(db, fixture.storage).upload_document(
            UploadFile(filename="synthetic.pdf", file=BytesIO(pdf())), user,
        )


