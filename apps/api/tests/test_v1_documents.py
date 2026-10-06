import uuid
from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.storage.object_storage import LocalStorage, get_object_storage
from tests.conftest import VALID_AUTH_HEADER

SAMPLE_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"


@pytest.fixture
def mock_storage(tmp_path):
    storage = LocalStorage(base_dir=str(tmp_path), bucket="test-v1-docs")
    return storage


@pytest.mark.asyncio
async def test_v1_document_lifecycle(mock_storage):
    """Test full document workflow: upload, registration, status check, signed-url, and deletion."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    cid = f"doc_{uuid.uuid4().hex[:6]}"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            # 1. Register a test client
            res = await client.post(
                "/api/v1/clients",
                json={
                    "client_id": cid,
                    "name": "Doc Test Client",
                    "allowed_scopes": ["documents:write", "documents:read", "documents:delete", "collections:manage"],
                    "authorized_tenants": ["*"],
                },
                headers=VALID_AUTH_HEADER,
            )
            assert res.status_code == 201
            api_key = res.json()["api_key"]

            auth_headers = {
                "Authorization": f"Bearer {api_key}",
                "X-Tenant-ID": "tenant_docs_1",
            }

            # 2. Create a collection
            coll_res = await client.post(
                "/api/v1/collections",
                json={"collection_id": "math_101", "name": "Math 101"},
                headers=auth_headers,
            )
            assert coll_res.status_code == 201

            # 3. Upload file bytes
            upload_res = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("algebra_ch1.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"document_id": "doc_alg_001", "collection_id": "math_101"},
                headers=auth_headers,
            )
            assert upload_res.status_code == 201, upload_res.text
            up_data = upload_res.json()
            assert "storage_key" in up_data
            assert up_data["mime_type"] == "application/pdf"
            assert up_data["file_size"] == len(SAMPLE_PDF_BYTES)

            # 4. Register document and enqueue
            reg_payload = {
                "external_document_id": "doc_alg_001",
                "storage_key": up_data["storage_key"],
                "file_name": "algebra_ch1.pdf",
                "mime_type": up_data["mime_type"],
                "file_size": up_data["file_size"],
                "collection_id": "math_101",
                "owner_subject_id": "teacher_bob",
                "document_type": "TEXTBOOK",
                "metadata": {"chapter": 1, "topic": "Polynomials"},
            }
            reg_res = await client.post(
                "/api/v1/documents",
                json=reg_payload,
                headers=auth_headers,
            )
            assert reg_res.status_code == 202, reg_res.text
            doc_data = reg_res.json()
            assert doc_data["external_document_id"] == "doc_alg_001"
            assert doc_data["status"] == "QUEUED"
            assert doc_data["client_id"] == cid
            assert doc_data["tenant_id"] == "tenant_docs_1"
            assert doc_data["collection_id"] == "math_101"
            assert doc_data["owner_subject_id"] == "teacher_bob"

            # 5. Get document by external ID
            get_res = await client.get("/api/v1/documents/doc_alg_001", headers=auth_headers)
            assert get_res.status_code == 200
            assert get_res.json()["external_document_id"] == "doc_alg_001"

            # 6. List documents in this tenant
            list_res = await client.get("/api/v1/documents?collection_id=math_101", headers=auth_headers)
            assert list_res.status_code == 200
            docs = list_res.json()["documents"]
            assert len(docs) == 1
            assert docs[0]["external_document_id"] == "doc_alg_001"

            # 7. Generate signed URL
            url_res = await client.get("/api/v1/documents/doc_alg_001/signed-url?expires_in=600", headers=auth_headers)
            assert url_res.status_code == 200
            assert "url" in url_res.json()

            # 8. Delete document
            del_res = await client.delete("/api/v1/documents/doc_alg_001", headers=auth_headers)
            assert del_res.status_code == 200
            assert del_res.json()["success"] is True

            # 9. Verify 404 after deletion
            get_after = await client.get("/api/v1/documents/doc_alg_001", headers=auth_headers)
            assert get_after.status_code == 404

    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_v1_document_validation(mock_storage):
    """Test security checks: path traversal rejection, unsupported formats, missing files."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    val_cid = f"val_{uuid.uuid4().hex[:6]}"
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            res = await client.post(
                "/api/v1/clients",
                json={
                    "client_id": val_cid,
                    "name": "Validation Client",
                    "allowed_scopes": ["documents:write", "documents:read"],
                    "authorized_tenants": ["*"],
                },
                headers=VALID_AUTH_HEADER,
            )
            assert res.status_code == 201
            api_key = res.json()["api_key"]
            auth_headers = {"Authorization": f"Bearer {api_key}", "X-Tenant-ID": "test_tenant"}

            # Empty file rejected
            up_empty = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("empty.pdf", b"", "application/pdf")},
                data={"document_id": "doc_empty"},
                headers=auth_headers,
            )
            assert up_empty.status_code == 400

            # Invalid file format (e.g. executable/binary)
            up_bin = await client.post(
                "/api/v1/documents/upload",
                files={"file": ("malicious.exe", b"MZ\x90\x00\x03\x00\x00\x00", "application/x-msdownload")},
                data={"document_id": "doc_bad"},
                headers=auth_headers,
            )
            assert up_bin.status_code == 400

            # Path traversal in register request rejected
            reg_traversal = await client.post(
                "/api/v1/documents",
                json={
                    "external_document_id": "doc_trav",
                    "storage_key": "../../../etc/passwd",
                    "file_name": "passwords.txt",
                    "mime_type": "text/plain",
                    "file_size": 100,
                },
                headers=auth_headers,
            )
            assert reg_traversal.status_code == 400
            assert "traversal" in reg_traversal.text.lower()
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
