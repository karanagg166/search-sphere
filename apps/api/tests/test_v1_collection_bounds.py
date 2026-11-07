"""Real database/API checks for collection-bound server delegation."""

import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from src.db import AsyncSessionLocal
from src.main import app
from src.models.collection import DocumentCollection
from src.models.service_client import ServiceClient


@pytest_asyncio.fixture
async def catalog(admin_auth_header):
    clients = ["synthetic_catalog_" + uuid.uuid4().hex[:16] for _ in range(2)]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as api:
        headers = []
        try:
            for client_id in clients:
                response = await api.post("/api/v1/clients", headers=admin_auth_header,
                    json={"client_id": client_id, "name": "Public synthetic catalog", "allowed_scopes": ["collections:manage"], "authorized_tenants": ["tenant-a", "tenant-b"]})
                assert response.status_code == 201
                headers.append({"Authorization": "Bearer " + response.json()["api_key"], "X-Client-ID": client_id, "X-Tenant-ID": "tenant-a"})
            for selected, tenant, collection in ((0, "tenant-a", "a"), (0, "tenant-a", "b"), (0, "tenant-b", "a"), (1, "tenant-a", "a")):
                response = await api.post("/api/v1/collections", headers={**headers[selected], "X-Tenant-ID": tenant}, json={"collection_id": collection, "name": "Public synthetic " + collection})
                assert response.status_code == 201
            yield SimpleNamespace(api=api, headers=headers, bound={**headers[0], "X-Collection-ID": "a"}, clients=clients)
        finally:
            async with AsyncSessionLocal() as db, db.begin():
                await db.execute(delete(DocumentCollection).where(DocumentCollection.client_id.in_(clients)))
                await db.execute(delete(ServiceClient).where(ServiceClient.client_id.in_(clients)))


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,expected", [(0, ["a"]), (1, [])])
async def test_bound_collection_filters_rows_and_count_before_paging(catalog, offset, expected):
    response = await catalog.api.get(f"/api/v1/collections?limit=1&offset={offset}", headers=catalog.bound)
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [row["collection_id"] for row in response.json()["collections"]] == expected


@pytest.mark.asyncio
async def test_nonexistent_collection_bound_cannot_list_other_collections(catalog):
    response = await catalog.api.get("/api/v1/collections", headers={**catalog.bound, "X-Collection-ID": "absent"})
    assert response.status_code == 200
    assert response.json() == {"total": 0, "collections": []}


@pytest.mark.asyncio
async def test_subject_delegation_cannot_expand_to_tenant_wide_catalog(catalog):
    response = await catalog.api.get("/api/v1/collections", headers={**catalog.headers[0], "X-Subject-ID": "synthetic-subject"})
    assert response.status_code == 403
    response = await catalog.api.get("/api/v1/collections", headers={**catalog.bound, "X-Subject-ID": "synthetic-subject"})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["collections"][0]["collection_id"] == "a"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "DELETE"])
async def test_conflicting_path_collection_is_denied_and_preserved(catalog, method):
    response = await catalog.api.request(method, "/api/v1/collections/b", headers=catalog.bound)
    assert response.status_code == 403
    assert "Public synthetic b" not in response.text
    assert (await catalog.api.get("/api/v1/collections/b", headers=catalog.headers[0])).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("collection", ["b", "new"])
async def test_conflicting_create_cannot_probe_or_write_another_collection(catalog, collection):
    response = await catalog.api.post("/api/v1/collections", headers=catalog.bound, json={"collection_id": collection, "name": "Public synthetic rejected"})
    assert response.status_code == 403
    after = await catalog.api.get("/api/v1/collections", headers=catalog.headers[0])
    assert {row["collection_id"] for row in after.json()["collections"]} == {"a", "b"}


@pytest.mark.asyncio
async def test_authorized_bound_crud_and_tenant_administration_continue(catalog):
    response = await catalog.api.get("/api/v1/collections/a", headers=catalog.bound)
    assert response.status_code == 200
    assert response.json()["client_id"] == catalog.clients[0]
    assert response.json()["tenant_id"] == "tenant-a"
    assert (await catalog.api.post("/api/v1/collections", headers=catalog.bound, json={"collection_id": "a", "name": "Public duplicate"})).status_code == 409
    assert (await catalog.api.delete("/api/v1/collections/a", headers=catalog.bound)).status_code == 200
    assert (await catalog.api.get("/api/v1/collections/a", headers=catalog.bound)).status_code == 404
    assert (await catalog.api.get("/api/v1/collections/a", headers={**catalog.headers[0], "X-Tenant-ID": "tenant-b"})).status_code == 200
    assert (await catalog.api.get("/api/v1/collections/a", headers=catalog.headers[1])).status_code == 200
    response = await catalog.api.post("/api/v1/collections", headers=catalog.bound, json={"collection_id": "a", "name": "Public synthetic recreated"})
    assert response.status_code == 201
    response = await catalog.api.get("/api/v1/collections", headers=catalog.headers[0])
    assert response.json()["total"] == 2
