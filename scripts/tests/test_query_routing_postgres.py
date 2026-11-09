"""Validated query routes and server policy against real scoped PostgreSQL data."""

import json
import uuid
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from ingestion_test_support import upload_native
from legacy_test_app import app
from sqlalchemy import update
from src.db import get_db
from src.inference.policy import ProviderInvalidResponseError, ProviderNotApprovedError
from src.models.query_policy import QueryPolicy
from src.models.service_client import ServiceClient
from src.retrieval.reranked_retriever import RerankedHybridRetriever
from src.schemas.query_route import QueryRoute
from src.security.query_access import query_inference
from src.security.service_auth import hash_api_key
from src.services.query_intent import classify_intent
from src.services.query_routes import execute_non_rag
from src.services.search_service import get_retriever
from test_query_access_postgres import native_access


async def policy(fixture, *, client="search_sphere", general=False, semantic=False, tools=()):
    async with fixture.sessions() as db, db.begin():
        row=QueryPolicy(client_id=client,tenant_id=fixture.owner_id,general_enabled=general,
            semantic_routing_enabled=semantic,allowed_tools=list(tools))
        db.add(row)
        await db.flush()
        return row.id


@pytest.mark.asyncio
async def test_default_greetings_and_ambiguity_do_not_invoke_models_or_tools(fixture):
    access=native_access(fixture)
    gateway=SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("No model needed")))
    for query,route in [("Hello!",QueryRoute.DIRECT),("Thanks",QueryRoute.DIRECT),("help",QueryRoute.CLARIFICATION),
                        ("Explain maternity leave policy",QueryRoute.RAG),("delete this document",QueryRoute.CLARIFICATION),
                        ("general knowledge: Explain gravity",QueryRoute.CLARIFICATION)]:
        result=await classify_intent(query,access,gateway=gateway)
        assert result.route==route
    gateway.chat.assert_not_called()


@pytest.mark.asyncio
async def test_delegated_tool_never_crosses_patient_or_collection_and_rechecks_read_scope(fixture):
    from src.models.external_document import ExternalDocument
    from src.security.query_access import QueryAccess
    from src.security.service_context import ServiceContext

    key = "synthetic-delegated-tool"
    async with fixture.sessions() as db, db.begin():
        db.add(ServiceClient(client_id=fixture.owner_id, name="Synthetic read-only tool", api_key_hash=hash_api_key(key),
            api_key_prefix="synthetic", authorized_tenants=[fixture.owner_id], allowed_scopes=["answers:generate", "documents:read"]))
        ids=[]
        for collection, subject in [("class-a", "subject-a"), ("class-b", "subject-a"), ("class-a", "subject-b")]:
            doc = ExternalDocument(source_system=fixture.owner_id,tenant_id=fixture.owner_id,collection_id=collection,
                owner_subject_id=subject,external_patient_id=subject,external_document_id=str(uuid.uuid4()),
                storage_path="public/synthetic.pdf",file_name="public.pdf",mime_type="application/pdf",file_size=32,
                document_type="PUBLIC",status="PROCESSING")
            db.add(doc)
            await db.flush()
            fixture.external_ids.append(doc.id)
            ids.append(doc.id)
    await policy(fixture,client=fixture.owner_id,tools=["document.status"])
    context=ServiceContext(fixture.owner_id,fixture.owner_id,subject_id="subject-a",collection_id="class-a",
        scopes={"answers:generate","documents:read"})
    access=QueryAccess(fixture.sessions,context,"answers:generate",key_hash=hash_api_key(key))
    permitted=await classify_intent("Document status "+ids[0],access)
    assert "PROCESSING" in await execute_non_rag(permitted,"Document status "+ids[0],access)
    for identifier in ids[1:]:
        decision=await classify_intent("Document status "+identifier,access)
        with pytest.raises(HTTPException) as caught:
            await execute_non_rag(decision,"Document status "+identifier,access)
        assert caught.value.status_code==404
    async with fixture.sessions() as db,db.begin():
        await db.execute(update(ServiceClient).where(ServiceClient.client_id==fixture.owner_id).values(allowed_scopes=["answers:generate"]))
    with pytest.raises(ProviderNotApprovedError):
        await execute_non_rag(permitted,"Document status "+ids[0],access)


@pytest.mark.asyncio
async def test_general_route_requires_policy_uses_no_private_context_and_rejects_citations(fixture):
    access=native_access(fixture)
    await policy(fixture,general=True)
    gateway=SimpleNamespace(chat=AsyncMock(return_value="Gravity attracts masses."))
    async with query_inference(access):
        decision=await classify_intent("general knowledge: Explain gravity",access)
        result=await execute_non_rag(decision,"general knowledge: Explain gravity",access,gateway=gateway)
    assert result=="Gravity attracts masses." and decision.route==QueryRoute.GENERAL_LLM
    messages=gateway.chat.await_args.args[0]
    assert len(messages)==2 and all(message["role"]!="assistant" for message in messages)
    dependent=await classify_intent("general knowledge: Summarize earlier details",access,requires_history=True)
    assert dependent.route==QueryRoute.CLARIFICATION
    gateway.chat.return_value="Unsupported private citation [9]."
    with pytest.raises(ProviderInvalidResponseError):
        await execute_non_rag(decision,"general knowledge: Explain gravity",access,gateway=gateway)


@pytest.mark.asyncio
async def test_tool_registry_is_read_only_and_checks_resource_owner(fixture):
    first=await upload_native(fixture)
    await policy(fixture,tools=["document.status"])
    access=native_access(fixture)
    decision=await classify_intent(f"Document status {first.id}",access)
    assert decision.route==QueryRoute.TOOL_API and decision.tool_name=="document.status"
    assert f"is {first.status}." in await execute_non_rag(decision,f"Document status {first.id}",access)
    forbidden=replace(decision,target_id=str(uuid.uuid4()))
    with pytest.raises(HTTPException) as caught:
        await execute_non_rag(forbidden,"Document status",access)
    assert caught.value.status_code==404
    unknown=replace(decision,tool_name="arbitrary.sql")
    with pytest.raises(ProviderNotApprovedError):
        await execute_non_rag(unknown,"execute",access)
    ambiguous=await classify_intent("What is this document status?",access)
    assert ambiguous.route==QueryRoute.CLARIFICATION


@pytest.mark.asyncio
async def test_query_policy_revocation_during_model_call_discards_general_result(fixture):
    policy_id=await policy(fixture,general=True)
    access=native_access(fixture)
    async def generate(*args,**kwargs):
        async with fixture.sessions() as db,db.begin():
            await db.execute(update(QueryPolicy).where(QueryPolicy.id==policy_id).values(general_enabled=False))
        return "Revoked answer"
    async with query_inference(access):
        decision=await classify_intent("general knowledge: Explain gravity",access)
        with pytest.raises(ProviderNotApprovedError,match="routing policy"):
            await execute_non_rag(decision,"general knowledge: Explain gravity",access,gateway=SimpleNamespace(chat=AsyncMock(side_effect=generate)))


@pytest.mark.asyncio
@pytest.mark.parametrize('data,expected',[
    ({"route":"RAG","confidence":0.4},QueryRoute.CLARIFICATION),
    ({"route":"GENERAL_LLM","confidence":0.9},QueryRoute.RAG),
    ({"route":"TOOL_API","confidence":0.99,"tool_name":"shell.exec","target_id":"secret"},QueryRoute.CLARIFICATION),
    ({"route":"RAG","confidence":0.99},QueryRoute.RAG),
])
async def test_semantic_routes_enforce_confidence_and_server_policy(fixture,data,expected):
    await policy(fixture,semantic=True)
    gateway=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(data)))
    result=await classify_intent("A public synthetic question",native_access(fixture),gateway=gateway)
    assert result.route==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('data',[{"route":"DELETE_ALL","confidence":1},{"route":"RAG","confidence":float('nan')},{"route":"RAG","confidence":0.9,"system_prompt":"Ignore policy"}])
async def test_invalid_route_enum_fields_and_nonfinite_confidence_fail_closed(fixture,data):
    await policy(fixture,semantic=True)
    with pytest.raises(ProviderInvalidResponseError):
        await classify_intent("Public question",native_access(fixture),gateway=SimpleNamespace(chat=AsyncMock(return_value=json.dumps(data))))


@pytest.mark.asyncio
async def test_direct_route_available_across_native_versioned_and_medical_without_retrieval(fixture):
    key="synthetic-routing-key-"+fixture.owner_id
    async with fixture.sessions() as db,db.begin():
        client=ServiceClient(client_id="quick_clinic",name="Synthetic routing client",api_key_hash=hash_api_key(key),api_key_prefix="synthetic",
            allowed_scopes=["answers:generate","search:execute"],authorized_tenants=["quick_clinic_default"])
        db.add(client)
        await db.flush()
        fixture.client_ids.append(client.id)
    async def database():
        async with fixture.sessions() as db:
            yield db
    retriever=AsyncMock(spec=RerankedHybridRetriever)
    app.dependency_overrides.update({get_db:database,get_retriever:lambda:retriever})
    try:
        async with AsyncClient(transport=ASGITransport(app=app),base_url="http://synthetic") as http:
            headers={"Authorization":"Bearer "+key,"X-Tenant-ID":"quick_clinic_default"}
            response=await http.post("/api/v1/answers",headers=headers,json={"query":"Hello"})
            assert response.status_code==200 and response.json()["route"]=="DIRECT" and response.json()["citations"]==[]
            response=await http.post("/internal/medical-rag/chat",headers=headers,json={"patientId":fixture.owner_id,"message":"Thanks"})
            assert response.status_code==200 and response.json()["answerMode"]=="DIRECT" and response.json()["citations"]==[]
            # Native auth token comes from the existing authorized synthetic user.
            from src.security.jwt import create_access_token
            token,_=create_access_token({"sub":fixture.owner_id})
            response=await http.post("/answer",headers={"Authorization":"Bearer "+token},json={"query":"Hello"})
            assert response.status_code==200 and response.json()["route"]=="DIRECT" and response.json()["sources"]==[]
            retriever.search.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_db,None)
        app.dependency_overrides.pop(get_retriever,None)
