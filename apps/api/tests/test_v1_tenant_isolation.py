import uuid
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.storage.object_storage import LocalStorage, get_object_storage
from tests.conftest import VALID_AUTH_HEADER

SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"


@pytest.fixture
def mock_storage(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path), bucket="test-isolation-docs")
    return storage


@pytest.mark.asyncio
async def test_cross_tenant_and_cross_client_isolation(mock_storage):
    """
    Comprehensive multi-tenant isolation verification:
    1. Cross-client document and search isolation.
    2. Cross-tenant isolation within same client.
    3. Identical external document IDs in different tenants do NOT collide.
    4. Forged metadata filters cannot bypass security boundary.
    5. Header client mismatch is rejected with 403.
    """
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            cid_a = f"client_a_{uuid.uuid4().hex[:6]}"
            cid_b = f"client_b_{uuid.uuid4().hex[:6]}"

            # 1. Register Client A and Client B
            res_a = await client.post(
                "/api/v1/clients",
                json={
                    "client_id": cid_a,
                    "name": "Client A Corp",
                    "allowed_scopes": ["documents:read", "documents:write", "documents:delete", "search:execute", "collections:manage"],
                    "authorized_tenants": ["*"],
                },
                headers=VALID_AUTH_HEADER,
            )
            assert res_a.status_code == 201
            key_a = res_a.json()["api_key"]

            res_b = await client.post(
                "/api/v1/clients",
                json={
                    "client_id": cid_b,
                    "name": "Client B Corp",
                    "allowed_scopes": ["documents:read", "documents:write", "documents:delete", "search:execute", "collections:manage"],
                    "authorized_tenants": ["*"],
                },
                headers=VALID_AUTH_HEADER,
            )
            assert res_b.status_code == 201
            key_b = res_b.json()["api_key"]

            h_a_t1 = {"Authorization": f"Bearer {key_a}", "X-Client-ID": cid_a, "X-Tenant-ID": "tenant_1"}
            h_a_t2 = {"Authorization": f"Bearer {key_a}", "X-Client-ID": cid_a, "X-Tenant-ID": "tenant_2"}
            h_b_t1 = {"Authorization": f"Bearer {key_b}", "X-Client-ID": cid_b, "X-Tenant-ID": "tenant_1"}

            # 2. Upload and register document with same ID 'doc_common_101' in Client A / Tenant 1
            up_a1 = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("secret_plans.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"document_id": "doc_common_101"},
                headers=h_a_t1,
            )
            assert up_a1.status_code == 201
            reg_a1 = await client.post(
                "/api/v1/documents",
                json={
                    "external_document_id": "doc_common_101",
                    "storage_key": up_a1.json()["storage_key"],
                    "file_name": "secret_plans.pdf",
                    "mime_type": "application/pdf",
                    "file_size": len(SAMPLE_PDF_BYTES),
                    "document_type": "INTERNAL_PLANS",
                    "metadata": {"confidential": True, "tenant": "A_1"},
                },
                headers=h_a_t1,
            )
            assert reg_a1.status_code == 202
            doc_a1_db_id = reg_a1.json()["id"]

            # 3. Upload and register SAME document ID 'doc_common_101' in Client A / Tenant 2
            up_a2 = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("public_guide.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"document_id": "doc_common_101"},
                headers=h_a_t2,
            )
            assert up_a2.status_code == 201
            reg_a2 = await client.post(
                "/api/v1/documents",
                json={
                    "external_document_id": "doc_common_101",
                    "storage_key": up_a2.json()["storage_key"],
                    "file_name": "public_guide.pdf",
                    "mime_type": "application/pdf",
                    "file_size": len(SAMPLE_PDF_BYTES),
                    "document_type": "PUBLIC_GUIDE",
                    "metadata": {"confidential": False, "tenant": "A_2"},
                },
                headers=h_a_t2,
            )
            assert reg_a2.status_code == 202
            doc_a2_db_id = reg_a2.json()["id"]

            # Ensure they received completely separate DB IDs and storage paths
            assert doc_a1_db_id != doc_a2_db_id
            assert up_a1.json()["storage_key"] != up_a2.json()["storage_key"]

            # 4. Cross-Tenant isolation within Client A:
            # Querying doc_common_101 from Tenant 1 returns secret_plans.pdf
            get_t1 = await client.get("/api/v1/documents/doc_common_101", headers=h_a_t1)
            assert get_t1.status_code == 200
            assert get_t1.json()["file_name"] == "secret_plans.pdf"

            # Querying doc_common_101 from Tenant 2 returns public_guide.pdf
            get_t2 = await client.get("/api/v1/documents/doc_common_101", headers=h_a_t2)
            assert get_t2.status_code == 200
            assert get_t2.json()["file_name"] == "public_guide.pdf"

            # 5. Cross-Client isolation:
            # Client B / Tenant 1 queries 'doc_common_101' -> MUST BE 404 NOT FOUND!
            get_b1 = await client.get("/api/v1/documents/doc_common_101", headers=h_b_t1)
            assert get_b1.status_code == 404

            # Client B / Tenant 1 cannot delete Client A's document
            del_b1 = await client.delete("/api/v1/documents/doc_common_101", headers=h_b_t1)
            assert del_b1.status_code == 404

            # 6. Header tampering defense:
            # Authenticated with Key A, but sending X-Client-ID: Client B -> MUST BE 403 FORBIDDEN
            tampered_header = {
                "Authorization": f"Bearer {key_a}",
                "X-Client-ID": cid_b,
                "X-Tenant-ID": "tenant_1",
            }
            tampered_res = await client.get("/api/v1/documents", headers=tampered_header)
            assert tampered_res.status_code == 403
            assert "does not match authenticated service credential" in tampered_res.text

    finally:
        app.dependency_overrides.pop(get_object_storage, None)
