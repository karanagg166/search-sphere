import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from tests.conftest import VALID_AUTH_HEADER


@pytest.mark.asyncio
async def test_service_client_lifecycle():
    """Verify registration, authentication, key rotation, and revocation for external service clients."""
    cid = f"exam_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Register a new service client for ExamArena
        reg_payload = {
            "client_id": cid,
            "name": "ExamArena Learning Platform",
            "allowed_scopes": ["documents:read", "documents:write", "search:execute", "answers:generate", "collections:manage"],
            "authorized_tenants": ["school_1", "school_2"],
            "rate_limit_per_minute": 300,
        }
        res = await client.post("/api/v1/clients", json=reg_payload, headers=VALID_AUTH_HEADER)
        assert res.status_code == 201, res.text
        data = res.json()
        assert data["client_id"] == cid
        assert "api_key" in data
        raw_key = data["api_key"]
        assert raw_key.startswith("ss_live_")

        # 2. Cannot register duplicate client_id
        res_dup = await client.post("/api/v1/clients", json=reg_payload, headers=VALID_AUTH_HEADER)
        assert res_dup.status_code == 409

        # 3. List clients
        list_res = await client.get("/api/v1/clients", headers=VALID_AUTH_HEADER)
        assert list_res.status_code == 200
        client_ids = [c["client_id"] for c in list_res.json()]
        assert cid in client_ids

        # 4. Authenticate using newly generated API key for a collection creation
        client_auth_header = {
            "Authorization": f"Bearer {raw_key}",
            "X-Client-ID": cid,
            "X-Tenant-ID": "school_1",
        }
        coll_res = await client.post(
            "/api/v1/collections",
            json={"collection_id": "math_grade_10", "name": "Grade 10 Mathematics"},
            headers=client_auth_header,
        )
        assert coll_res.status_code == 201, coll_res.text
        assert coll_res.json()["collection_id"] == "math_grade_10"

        # 5. Unauthorized tenant access is blocked
        unauthorized_tenant_header = {
            "Authorization": f"Bearer {raw_key}",
            "X-Client-ID": cid,
            "X-Tenant-ID": "unauthorized_school_99",
        }
        coll_unauth = await client.post(
            "/api/v1/collections",
            json={"collection_id": "biology", "name": "Biology"},
            headers=unauthorized_tenant_header,
        )
        assert coll_unauth.status_code == 403

        # 6. Rotate API key
        rot_res = await client.post(
            f"/api/v1/clients/{cid}/rotate-key",
            headers=VALID_AUTH_HEADER,
        )
        assert rot_res.status_code == 200
        new_key = rot_res.json()["api_key"]
        assert new_key != raw_key

        # 7. Old key must now be rejected
        old_auth_header = {
            "Authorization": f"Bearer {raw_key}",
            "X-Tenant-ID": "school_1",
        }
        res_old = await client.get("/api/v1/collections", headers=old_auth_header)
        assert res_old.status_code == 401

        # 8. New key must work
        new_auth_header = {
            "Authorization": f"Bearer {new_key}",
            "X-Tenant-ID": "school_1",
        }
        res_new = await client.get("/api/v1/collections", headers=new_auth_header)
        assert res_new.status_code == 200

        # 9. Revoke client
        rev_res = await client.delete(f"/api/v1/clients/{cid}", headers=VALID_AUTH_HEADER)
        assert rev_res.status_code == 200
        assert rev_res.json()["status"] == "revoked"

        # 10. Revoked client key must now return 401
        res_revoked = await client.get("/api/v1/collections", headers=new_auth_header)
        assert res_revoked.status_code == 401


@pytest.mark.asyncio
async def test_service_client_scope_restriction():
    """Verify that scopes restrict what endpoints a service client can invoke."""
    ro_cid = f"readonly_{uuid.uuid4().hex[:6]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Register a read-only client (only search:execute)
        reg_payload = {
            "client_id": ro_cid,
            "name": "Readonly Searcher",
            "allowed_scopes": ["search:execute"],
            "authorized_tenants": ["*"],
        }
        res = await client.post("/api/v1/clients", json=reg_payload, headers=VALID_AUTH_HEADER)
        assert res.status_code == 201
        raw_key = res.json()["api_key"]

        read_auth_header = {
            "Authorization": f"Bearer {raw_key}",
            "X-Tenant-ID": "tenant_a",
        }

        # Attempting to create a collection (requires collections:manage) must return 403
        coll_res = await client.post(
            "/api/v1/collections",
            json={"collection_id": "unauthorized_coll", "name": "Illegal Coll"},
            headers=read_auth_header,
        )
        assert coll_res.status_code == 403
        assert "collections:manage" in coll_res.text
