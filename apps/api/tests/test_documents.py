import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.storage.object_storage import LocalStorage, get_object_storage


@pytest.fixture
def mock_storage(tmp_path):
    """Provides a fresh LocalStorage instance for tests to avoid network calls."""
    storage = LocalStorage(base_dir=str(tmp_path), bucket="test-documents")
    return storage


@pytest.mark.asyncio
async def test_unauthenticated_upload_rejected():
    """Upload must fail with 401 if user is not signed in."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        files = {
            "file": (
                "test.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF",
                "application/pdf",
            )
        }
        resp = await client.post("/documents", files=files)
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_pdf_upload_and_lifecycle(mock_storage):
    """Full authenticated flow: signup -> upload valid PDF -> get metadata -> list -> delete."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Sign up user
            unique_email = f"docuser_{uuid.uuid4().hex[:8]}@testsphere.io"
            signup_resp = await client.post(
                "/auth/signup",
                json={
                    "name": "Doc Owner",
                    "email": unique_email,
                    "password": "Password123!",
                },
            )
            assert signup_resp.status_code == 201
            token = signup_resp.json()["access_token"]
            auth_headers = {"Authorization": f"Bearer {token}"}

            # 2. Upload valid PDF
            pdf_bytes = b"%PDF-1.5\n%Header info\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"
            files = {"file": ("sample_report.pdf", pdf_bytes, "application/pdf")}
            upload_resp = await client.post(
                "/documents", files=files, headers=auth_headers
            )
            assert upload_resp.status_code == 201
            doc_data = upload_resp.json()

            assert doc_data["filename"] == "sample_report.pdf"
            assert doc_data["file_size"] == len(pdf_bytes)
            assert doc_data["mime_type"] == "application/pdf"
            assert doc_data["status"] == "uploaded"
            assert "documents/" in doc_data["storage_key"]
            assert doc_data["file_url"] is not None
            document_id = doc_data["id"]

            # Verify file exists in object storage
            assert await mock_storage.exists(doc_data["storage_key"]) is True

            # 3. Get document details
            get_resp = await client.get(
                f"/documents/{document_id}", headers=auth_headers
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == document_id

            # 4. List user documents
            list_resp = await client.get("/documents", headers=auth_headers)
            assert list_resp.status_code == 200
            list_data = list_resp.json()
            assert list_data["total"] >= 1
            assert any(d["id"] == document_id for d in list_data["documents"])

            # 5. User isolation: Another user cannot see this document
            other_email = f"other_{uuid.uuid4().hex[:8]}@testsphere.io"
            other_signup = await client.post(
                "/auth/signup",
                json={
                    "name": "Other User",
                    "email": other_email,
                    "password": "Password123!",
                },
            )
            other_token = other_signup.json()["access_token"]
            other_headers = {"Authorization": f"Bearer {other_token}"}

            other_get = await client.get(
                f"/documents/{document_id}", headers=other_headers
            )
            assert other_get.status_code == 404

            # 6. Delete document
            del_resp = await client.delete(
                f"/documents/{document_id}", headers=auth_headers
            )
            assert del_resp.status_code == 204

            # Verify removed from database and storage
            verify_del = await client.get(
                f"/documents/{document_id}", headers=auth_headers
            )
            assert verify_del.status_code == 404
            assert await mock_storage.exists(doc_data["storage_key"]) is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_upload_invalid_non_pdf(mock_storage):
    """File without %PDF- magic bytes must be rejected."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_email = f"docuser_{uuid.uuid4().hex[:8]}@testsphere.io"
            signup_resp = await client.post(
                "/auth/signup",
                json={"email": unique_email, "password": "Password123!"},
            )
            token = signup_resp.json()["access_token"]
            auth_headers = {"Authorization": f"Bearer {token}"}

            # File with .pdf name but plain text content
            fake_pdf = b"Hello, this is just a plain text file, not a PDF!"
            files = {"file": ("malicious.pdf", fake_pdf, "application/pdf")}
            resp = await client.post("/documents", files=files, headers=auth_headers)
            assert resp.status_code == 400
            assert "Invalid file format" in resp.json()["detail"]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_upload_empty_file(mock_storage):
    """Empty 0-byte file must be rejected."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unique_email = f"docuser_{uuid.uuid4().hex[:8]}@testsphere.io"
            signup_resp = await client.post(
                "/auth/signup",
                json={"email": unique_email, "password": "Password123!"},
            )
            token = signup_resp.json()["access_token"]
            auth_headers = {"Authorization": f"Bearer {token}"}

            files = {"file": ("empty.pdf", b"", "application/pdf")}
            resp = await client.post("/documents", files=files, headers=auth_headers)
            assert resp.status_code == 400
            assert "empty" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()
