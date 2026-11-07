"""Medical adapter deletion uses doctor/patient scope without inference."""

import pytest
from sqlalchemy import func, select, update
from src.models.conversation import Conversation
from src.models.conversation_memory import ConversationMemory
from src.models.message import Message
from src.models.service_client import ServiceClient
from src.security.medical_context import patient_collection_id
from test_medical_context_postgres import chat
from test_medical_context_postgres import fixture as fixture  # noqa: PLC0414
from test_medical_context_postgres import medical as medical  # noqa: PLC0414


async def scopes(fixture, medical, allowed):
    async with fixture.sessions() as db, db.begin():
        await db.execute(update(ServiceClient).where(ServiceClient.id.in_(fixture.client_ids)).values(allowed_scopes=allowed))


def headers(medical):
    return {**medical.headers, "X-Subject-ID": medical.subject, "X-Collection-ID": patient_collection_id(medical.subject)}


@pytest.mark.asyncio
async def test_deletion_removes_medical_turns_memory_and_fences_late_chat(fixture, medical):
    assert (await chat(medical, "What is the latest blood pressure reading?")).status_code == 200
    async with fixture.sessions() as db:
        cid = await db.scalar(select(Conversation.id).where(Conversation.external_reference == medical.reference))
        assert await db.get(ConversationMemory, cid) is not None
    await scopes(fixture, medical, ["answers:generate", "documents:delete"])
    for _ in range(2):
        result = await medical.http.delete("/internal/medical-rag/conversations/" + medical.reference, headers=headers(medical))
        assert result.status_code == 200 and result.json() == {"deleted": True}
    async with fixture.sessions() as db:
        assert await db.get(Conversation, cid) is None
        assert await db.get(ConversationMemory, cid) is None
        assert await db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == cid)) == 0
    late = await chat(medical, "What about that reading?")
    assert late.status_code == 409
    medical.generator.generate_answer.assert_not_called()
    medical.provider.rewrite.assert_not_called()
    assert fixture.calls == []


@pytest.mark.asyncio
async def test_deleting_local_only_thread_does_not_create_upstream_memory(fixture, medical):
    await scopes(fixture, medical, ["answers:generate", "documents:delete"])
    result = await medical.http.delete("/internal/medical-rag/conversations/" + medical.reference, headers=headers(medical))
    assert result.status_code == 200
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation).where(
            Conversation.external_reference == medical.reference)) == 0
    assert (await chat(medical, "What is the latest blood pressure reading?")).status_code == 409
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("remove_header", ["Authorization", "X-Subject-ID", "X-Actor-ID"])
async def test_delete_requires_authenticated_subject_and_actor(fixture, medical, remove_header):
    await scopes(fixture, medical, ["answers:generate", "documents:delete"])
    value = headers(medical)
    value.pop(remove_header)
    response = await medical.http.delete("/internal/medical-rag/conversations/" + medical.reference, headers=value)
    assert response.status_code == (401 if remove_header == "Authorization" else 422)
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("allowed", [["documents:delete"], ["answers:generate"], ["conversations:write"]])
async def test_delete_requires_both_existing_medical_capabilities(fixture, medical, allowed):
    await scopes(fixture, medical, allowed)
    response = await medical.http.delete("/internal/medical-rag/conversations/" + medical.reference, headers=headers(medical))
    assert response.status_code == 403
    assert fixture.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changes,expected", [
    ({"X-Client-ID": "exam_arena"}, 403), ({"X-Tenant-ID": "another-clinic"}, 403),
    ({"X-Collection-ID": "another-patient-records"}, 403),
    ({"X-Actor-ID": "another-doctor"}, 200), ({"X-Subject-ID": "another-patient", "X-Collection-ID": None}, 200),
])
async def test_delete_cannot_remove_another_doctor_or_patients_thread(fixture, medical, changes, expected):
    assert (await chat(medical, "What is the latest blood pressure reading?")).status_code == 200
    await scopes(fixture, medical, ["answers:generate", "documents:delete"])
    value = {**headers(medical), **changes}
    value = {key: item for key, item in value.items() if item is not None}
    result = await medical.http.delete("/internal/medical-rag/conversations/" + medical.reference, headers=value)
    assert result.status_code == expected
    async with fixture.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Conversation).where(
            Conversation.external_reference == medical.reference, Conversation.actor_id == "synthetic-doctor-a",
            Conversation.subject_id == medical.subject)) == 1
    assert fixture.calls == []
