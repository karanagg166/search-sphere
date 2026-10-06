import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.conftest import VALID_AUTH_HEADER


@pytest.mark.asyncio
async def test_collections_crud_and_tenant_scoping():
    """Verify collections creation, retrieval, listing, deletion, and cross-tenant separation."""
    cid_a = f"alpha_{uuid.uuid4().hex[:6]}"
    cid_b = f"beta_{uuid.uuid4().hex[:6]}"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register two clients for testing
        res_a = await client.post(
            "/api/v1/clients",
            json={"client_id": cid_a, "name": "App Alpha", "allowed_scopes": ["collections:manage"], "authorized_tenants": ["*"]},
            headers=VALID_AUTH_HEADER,
        )
        assert res_a.status_code == 201
        key_a = res_a.json()["api_key"]

        res_b = await client.post(
            "/api/v1/clients",
            json={"client_id": cid_b, "name": "App Beta", "allowed_scopes": ["collections:manage"], "authorized_tenants": ["*"]},
            headers=VALID_AUTH_HEADER,
        )
        assert res_b.status_code == 201
        key_b = res_b.json()["api_key"]

        # Tenant 1 on App Alpha
        h_alpha_t1 = {"Authorization": f"Bearer {key_a}", "X-Tenant-ID": "tenant_1"}
        # Tenant 2 on App Alpha
        h_alpha_t2 = {"Authorization": f"Bearer {key_a}", "X-Tenant-ID": "tenant_2"}
        # Tenant 1 on App Beta
        h_beta_t1 = {"Authorization": f"Bearer {key_b}", "X-Tenant-ID": "tenant_1"}

        # 1. Create collection 'research_papers' in App Alpha / Tenant 1
        c1 = await client.post(
            "/api/v1/collections",
            json={"collection_id": "research_papers", "name": "AI Research Papers", "description": "ArXiv drafts"},
            headers=h_alpha_t1,
        )
        assert c1.status_code == 201
        assert c1.json()["collection_id"] == "research_papers"
        assert c1.json()["client_id"] == cid_a
        assert c1.json()["tenant_id"] == "tenant_1"

        # 2. Cannot duplicate collection in same tenant
        c1_dup = await client.post(
            "/api/v1/collections",
            json={"collection_id": "research_papers", "name": "Duplicate Papers"},
            headers=h_alpha_t1,
        )
        assert c1_dup.status_code == 409

        # 3. Same collection_id 'research_papers' CAN be created in Tenant 2 (isolated namespace)
        c2 = await client.post(
            "/api/v1/collections",
            json={"collection_id": "research_papers", "name": "Tenant 2 Papers"},
            headers=h_alpha_t2,
        )
        assert c2.status_code == 201
        assert c2.json()["tenant_id"] == "tenant_2"

        # 4. Same collection_id 'research_papers' CAN be created in App Beta / Tenant 1 (isolated client namespace)
        c3 = await client.post(
            "/api/v1/collections",
            json={"collection_id": "research_papers", "name": "Beta Papers"},
            headers=h_beta_t1,
        )
        assert c3.status_code == 201
        assert c3.json()["client_id"] == cid_b

        # 5. List collections in Alpha / Tenant 1 only returns Alpha / Tenant 1 collections
        list_res = await client.get("/api/v1/collections", headers=h_alpha_t1)
        assert list_res.status_code == 200
        coll_ids = [c["collection_id"] for c in list_res.json()["collections"]]
        assert "research_papers" in coll_ids
        for c in list_res.json()["collections"]:
            assert c["client_id"] == cid_a
            assert c["tenant_id"] == "tenant_1"

        # 6. Delete collection in Alpha / Tenant 1
        del_res = await client.delete("/api/v1/collections/research_papers", headers=h_alpha_t1)
        assert del_res.status_code == 200

        # Alpha / Tenant 1 now has 404 for 'research_papers'
        get_res = await client.get("/api/v1/collections/research_papers", headers=h_alpha_t1)
        assert get_res.status_code == 404

        # BUT Alpha / Tenant 2 still has its collection untouched!
        get_t2 = await client.get("/api/v1/collections/research_papers", headers=h_alpha_t2)
        assert get_t2.status_code == 200
