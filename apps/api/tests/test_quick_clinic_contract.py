"""Synthetic API contract regression tests; providers are mocked explicitly."""
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.db import AsyncSessionLocal
from src.main import app
from src.models.service_client import ServiceClient
from src.security.service_auth import generate_api_key, verify_service_secret
from src.security.service_context import ServiceContext
from src.security.medical_context import patient_collection_id
from src.services.search_service import get_retriever
from src.services.answer_generator import get_answer_generator
from src.storage.object_storage import get_object_storage


@pytest.fixture
async def service_keys():
    keys = {}
    ids = []
    async with AsyncSessionLocal() as session:
        for client_id, revoked in (("quick_clinic", False), ("exam_arena_test_client", False), ("revoked_test_client", True)):
            raw, prefix, hashed = generate_api_key()
            row = ServiceClient(
                client_id=client_id, name="Synthetic contract client", api_key_hash=hashed,
                api_key_prefix=prefix, allowed_scopes=["documents:read", "documents:write", "documents:delete", "search:execute", "answers:generate", "collections:manage"],
                authorized_tenants=["quick_clinic_default"],
                revoked_at=datetime.now(timezone.utc) if revoked else None,
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
            keys[client_id] = raw
        await session.commit()
    yield keys
    async with AsyncSessionLocal() as session:
        await session.execute(delete(ServiceClient).where(ServiceClient.id.in_(ids)))
        await session.commit()


@pytest.mark.asyncio
async def test_authentication_matrix(service_keys):
    storage = AsyncMock()
    storage.create_signed_url.return_value = "https://example.invalid/synthetic"
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Authorization": f"Bearer {service_keys['quick_clinic']}", "X-Client-ID": "quick_clinic", "X-Tenant-ID": "quick_clinic_default", "X-Subject-ID": "synthetic-patient-a"}
            path = "/internal/medical-documents/signed-url?storagePath=medical-documents/synthetic-patient-a/doc/report.pdf"
            assert (await client.get(path, headers=headers)).status_code == 200
            for changes, expected in (
                ({"Authorization": "Bearer invalid-test-token"}, 401),
                ({"Authorization": f"Bearer {service_keys['revoked_test_client']}"}, 401),
                ({"X-Client-ID": "exam_arena"}, 403),
                ({"X-Tenant-ID": "unauthorized"}, 403),
                ({"Authorization": f"Bearer {service_keys['exam_arena_test_client']}", "X-Client-ID": "exam_arena_test_client"}, 403),
                ({"X-Subject-ID": "synthetic-patient-b"}, 403),
                ({"X-Collection-ID": patient_collection_id("synthetic-patient-b")}, 403),
            ):
                assert (await client.get(path, headers={**headers, **changes})).status_code == expected
            assert (await client.get(path)).status_code == 401
            with patch("src.security.service_auth.settings.QUICK_CLINIC_SERVICE_SECRET", None):
                assert (await client.get(path, headers={**headers, "Authorization": "Bearer invalid-test-token"})).status_code == 401
                async with AsyncSessionLocal() as session:
                    with pytest.raises(Exception) as caught:
                        await verify_service_secret("Bearer invalid-test-token", session)
                    assert caught.value.status_code == 401
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_request_scope_cannot_be_overridden_before_retrieval(service_keys):
    retriever, generator = AsyncMock(), AsyncMock()
    app.dependency_overrides[get_retriever] = lambda: retriever
    app.dependency_overrides[get_answer_generator] = lambda: generator
    try:
        headers = {"Authorization": f"Bearer {service_keys['quick_clinic']}", "X-Tenant-ID": "quick_clinic_default", "X-Subject-ID": "synthetic-patient-a", "X-Collection-ID": patient_collection_id("synthetic-patient-a")}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            for body in ({"query": "glucose", "owner_subject_id": "synthetic-patient-b"}, {"query": "glucose", "collection_id": patient_collection_id("synthetic-patient-b")}):
                assert (await client.post("/api/v1/search", json=body, headers=headers)).status_code == 403
            assert (await client.post("/internal/medical-retrieval/search", json={"query": "glucose", "patientId": "synthetic-patient-b"}, headers=headers)).status_code == 403
        retriever.search.assert_not_awaited()
        generator.generate_answer.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_retriever, None)
        app.dependency_overrides.pop(get_answer_generator, None)


def test_scope_metadata_cannot_replace_mandatory_headers():
    from src.services.generic_rag_service import GenericRagService
    context = ServiceContext(client_id="quick_clinic", tenant_id="quick_clinic_default", subject_id="synthetic-patient-a")
    filters = GenericRagService(None, None, None)._build_qdrant_filters(context, metadata_filters={"client_id": "exam_arena", "owner_subject_id": "synthetic-patient-b"})
    assert filters == {"client_id": "quick_clinic", "tenant_id": "quick_clinic_default", "owner_subject_id": "synthetic-patient-a"}
