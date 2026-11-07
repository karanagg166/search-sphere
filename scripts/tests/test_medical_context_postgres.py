"""Authorized clinical history with real documents, observations and conversations."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import func, select, update
from src.db import get_db
from src.inference.policy import ProviderQuotaExceededError
from src.main import app
from src.models.conversation import Conversation
from src.models.document_chunk import DocumentChunk
from src.models.external_document import ExternalDocument
from src.models.medical_observation import MedicalObservation
from src.models.message import Message
from src.models.service_client import ServiceClient
from src.processing.models.search import RerankedSearchResult
from src.schemas.search import RewriteResult
from src.security.medical_context import patient_collection_id
from src.security.query_access import current_query_access
from src.services.answer_generator import get_answer_generator
from src.services.query_rewriter import QueryRewriter, get_query_rewriter
from src.services.search_service import get_retriever


@pytest_asyncio.fixture
async def medical(fixture):
    key = "synthetic-medical-" + fixture.owner_id
    subject = fixture.owner_id
    content = "PUBLIC SYNTHETIC physician note: rest for two days. Marker CLINIC-437."
    point = str(uuid.uuid4())
    await approve(fixture, "quick_clinic", "quick_clinic_default", subject, patient_collection_id(subject))
    async with fixture.sessions() as db, db.begin():
        client = ServiceClient(client_id="quick_clinic", name="Synthetic clinical authorization", api_key_prefix="synthetic",
            api_key_hash=hashlib.sha256(key.encode()).hexdigest(), authorized_tenants=["quick_clinic_default"],
            allowed_scopes=["answers:generate", "search:execute", "documents:read"])
        db.add(client)
        await db.flush()
        fixture.client_ids.append(client.id)
        docs = []
        for patient, reading in [(subject, "120/80"), (subject + "b", "185/110")]:
            doc = ExternalDocument(source_system="quick_clinic", tenant_id="quick_clinic_default", owner_subject_id=patient,
                collection_id=patient_collection_id(patient), external_patient_id=patient, external_document_id=str(uuid.uuid4()),
                file_name="public-synthetic.pdf", storage_path="public/synthetic.pdf", mime_type="application/pdf",
                file_size=32, document_type="PUBLIC", status="READY")
            db.add(doc)
            await db.flush()
            docs.append(doc)
            fixture.external_ids.append(doc.id)
            observation = MedicalObservation(id=str(uuid.uuid4()), source_system="quick_clinic", tenant_id="quick_clinic_default",
                external_patient_id=patient, external_document_id=doc.external_document_id, observation_type="BLOOD_PRESSURE",
                display_name="Blood Pressure", value_text=reading, unit="mmHg", observed_at=datetime.now(UTC)-timedelta(days=1),
                page_number=2, chunk_index=0, confidence=1.0, extraction_method="REGEX")
            db.add(observation)
            fixture.observation_ids.append(observation.id)
        db.add(DocumentChunk(point_id=point, document_id=docs[0].external_document_id, chunk_index=0, content=content,
            embedding=[1.0]+[0.0]*383, client_id="quick_clinic", source_system="quick_clinic", tenant_id="quick_clinic_default",
            collection_id=patient_collection_id(subject), owner_subject_id=subject, patient_id=subject, start_page=2, end_page=2,
            page_numbers=[2], block_types=["text"], metadata_json={"embedding_model":"embed-english-light-v3.0", "source_record_id":docs[0].id}))
    chunk = RerankedSearchResult(point, docs[0].external_document_id, 0, content, 20, 2, 2, [2], ["text"], 0.9,
        client_id="quick_clinic", source_system="quick_clinic", tenant_id="quick_clinic_default", patient_id=subject,
        collection_id=patient_collection_id(subject), owner_subject_id=subject)
    async def retrieve(**kwargs):
        return await current_query_access().authorize_candidates([chunk])
    retriever = SimpleNamespace(search=AsyncMock(side_effect=retrieve))
    generator = SimpleNamespace(generate_answer=AsyncMock(return_value=("The physician note says to rest for two days [1].", [chunk])))
    provider = SimpleNamespace(rewrite=AsyncMock(side_effect=lambda query, conversation_context: RewriteResult(
        original_query=query, retrieval_query="What physician instructions are in this patient's note?", rewritten=True)))
    async def database():
        async with fixture.sessions() as db:
            yield db
    app.dependency_overrides.update({get_db:database, get_retriever:lambda:retriever, get_answer_generator:lambda:generator,
                                    get_query_rewriter:lambda:QueryRewriter(provider=provider)})
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as http:
            yield SimpleNamespace(http=http, subject=subject, headers={"Authorization":"Bearer "+key, "X-Tenant-ID":"quick_clinic_default",
                "X-Actor-ID":"synthetic-doctor-a"}, reference=fixture.owner_id, doc=docs[0], chunk=chunk,
                retriever=retriever, generator=generator, provider=provider)
    finally:
        for dependency in [get_db,get_retriever,get_answer_generator,get_query_rewriter]:
            app.dependency_overrides.pop(dependency,None)


async def chat(value, query, *, headers=None, patient=None, stream=False, **extra):
    return await value.http.post("/internal/medical-rag/chat" + ("/stream" if stream else ""), headers=headers or value.headers,
        json={"patientId":patient or value.subject,"conversationId":value.reference,"message":query,**extra})


@pytest.mark.asyncio
async def test_temporal_followup_uses_authorized_observations_without_model_inference(fixture, medical):
    first = await chat(medical,"What is the latest blood pressure reading?")
    assert first.status_code == 200 and "120/80" in first.json()["answer"] and "185/110" not in first.text
    second = await chat(medical,"Only the last 3 days.")
    assert second.status_code == 200 and second.json()["rewritten"] and second.json()["requiresHistory"]
    assert "120/80" in second.json()["answer"] and second.json()["answerMode"] == "STRUCTURED"
    medical.provider.rewrite.assert_not_called()
    medical.generator.generate_answer.assert_not_called()
    assert fixture.calls == []
    async with fixture.sessions() as db:
        cid = (await db.execute(select(Conversation.id).where(Conversation.external_reference==medical.reference))).scalar_one()
        messages=(await db.execute(select(Message).where(Message.conversation_id==cid,Message.role=="assistant"))).scalars().all()
        assert len(messages)==2 and all(message.sources[0]["source_type"]=="OBSERVATION" for message in messages)
        assert all("content" not in message.sources[0] for message in messages)


@pytest.mark.asyncio
async def test_doctor_and_patient_history_isolation_and_caller_history_rejection(fixture, medical):
    first = await chat(medical,"What did the physician note say?",history=[{"role":"assistant","content":"OTHER PRIVATE HISTORY"}])
    assert first.status_code == 200
    assert medical.generator.generate_answer.await_args.kwargs["conversation_context"] == []
    followup = await chat(medical,"What about the physician instructions?")
    assert followup.status_code==200 and followup.json()["requiresHistory"]
    assert "OTHER PRIVATE HISTORY" not in str(medical.provider.rewrite.await_args)
    for headers,patient in [({**medical.headers,"X-Actor-ID":"synthetic-doctor-b"},medical.subject),
                            (medical.headers,medical.subject+"b")]:
        other=await chat(medical,"What about the physician instructions?",headers=headers,patient=patient)
        assert other.status_code==200 and other.json()["answerMode"]=="CLARIFICATION"
        assert other.json()["citations"]==[] and "rest for two days" not in other.json()["answer"]
    missing=await chat(medical,"What about the note?",headers={key:value for key,value in medical.headers.items() if key!="X-Actor-ID"})
    assert missing.status_code==422


@pytest.mark.asyncio
async def test_failed_document_cannot_supply_old_observations_or_history(fixture, medical):
    assert (await chat(medical,"What is the latest blood pressure reading?")).status_code==200
    async with fixture.sessions() as db,db.begin():
        await db.execute(update(ExternalDocument).where(ExternalDocument.id==medical.doc.id).values(status="FAILED"))
    followup=await chat(medical,"Only the last 3 days.")
    assert followup.status_code==200 and followup.json()["answerMode"]=="CLARIFICATION"
    assert "120/80" not in followup.json()["answer"]
    fresh=await chat(medical,"What is the latest blood pressure reading?")
    assert fresh.status_code==200 and fresh.json()["citations"]==[] and "120/80" not in fresh.json()["answer"]


@pytest.mark.asyncio
async def test_provider_failure_before_stream_emits_no_content_or_saved_turn(fixture, medical):
    medical.generator.generate_answer.side_effect=ProviderQuotaExceededError("PRIVATE SYNTHETIC DIAGNOSTIC")
    response=await chat(medical,"What did the physician note say?",stream=True)
    assert response.status_code==503 and response.json()["error_code"]=="provider_quota"
    assert "PRIVATE SYNTHETIC DIAGNOSTIC" not in response.text and "event: token" not in response.text
    medical.generator.generate_answer.assert_awaited_once()
    async with fixture.sessions() as db:
        cid=(await db.execute(select(Conversation.id).where(Conversation.external_reference==medical.reference))).scalar_one()
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==cid))==0


@pytest.mark.asyncio
async def test_orphaned_and_unreviewed_ocr_observations_cannot_supply_exact_values(fixture, medical):
    async with fixture.sessions() as db, db.begin():
        for document, method in [("missing-document", "REGEX"), (medical.doc.external_document_id, "OCR")]:
            row=MedicalObservation(id=str(uuid.uuid4()),source_system="quick_clinic",tenant_id="quick_clinic_default",
                external_patient_id=medical.subject,external_document_id=document,observation_type="BLOOD_PRESSURE",
                display_name="Blood Pressure",value_text="999/999",unit="mmHg",observed_at=datetime.now(UTC),extraction_method=method)
            db.add(row)
            fixture.observation_ids.append(row.id)
    result=await chat(medical,"What is the latest blood pressure reading?")
    assert result.status_code==200
    payload=result.json()
    assert "120/80" in payload["answer"] and "999/999" not in payload["answer"]
    assert all("999/999" not in (citation.get("content") or "") for citation in payload["citations"])
    assert all(citation.get("observationId") not in fixture.observation_ids[-2:] for citation in payload["citations"])


@pytest.mark.asyncio
async def test_source_revocation_after_generation_prevents_return_and_save(fixture, medical):
    async def revoked(**kwargs):
        async with fixture.sessions() as db,db.begin():
            await db.execute(update(ExternalDocument).where(ExternalDocument.id==medical.doc.id).values(status="FAILED"))
        return "Stale private answer [1].",[medical.chunk]
    medical.generator.generate_answer.side_effect=revoked
    response=await chat(medical,"What did the physician note say?")
    assert response.status_code==503 and response.json()["error_code"]=="provider_not_approved"
    assert "Stale private answer" not in response.text
    async with fixture.sessions() as db:
        cid=(await db.execute(select(Conversation.id).where(Conversation.external_reference==medical.reference))).scalar_one()
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id==cid))==0


@pytest.mark.asyncio
async def test_medical_sse_completion_matches_tokens_citations_and_persisted_message(fixture, medical):
    import json
    response=await chat(medical,"What did the physician note say?",stream=True)
    assert response.status_code==200 and "text/event-stream" in response.headers["content-type"]
    frames={}
    for frame in response.text.strip().split("\n\n"):
        kind,_,data=frame.partition("\ndata: ")
        frames[kind.removeprefix("event: ")]=json.loads(data)
    assert frames["token"]["text"]==frames["done"]["answer"]
    assert frames["citations"]["citations"]==frames["done"]["citations"]
    assert frames["done"]["messageId"]
    assert frames["done"]["citations"][0]["documentId"]==medical.doc.external_document_id
