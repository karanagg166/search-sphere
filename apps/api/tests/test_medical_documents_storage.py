from tests.binary_fixtures import pdf_bytes, image_bytes
from unittest.mock import patch
import pytest
from httpx import ASGITransport, AsyncClient

from src.config import settings
from src.main import app
from src.storage.object_storage import LocalStorage, get_object_storage

# Test Service Secret (test fixture only, no production fallback)
TEST_SERVICE_SECRET = "test-service-secret"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer completely-wrong-secret"}


@pytest.fixture(autouse=True)
def ensure_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured during tests."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


@pytest.fixture
def mock_storage(tmp_path):
    """Provides an isolated LocalStorage backend for medical document tests."""
    storage = LocalStorage(base_dir=str(tmp_path), bucket="test-medical-docs")
    return storage


# Valid sample file bytes
SAMPLE_PDF_BYTES = pdf_bytes()
SAMPLE_JPEG_BYTES = image_bytes("JPEG")
SAMPLE_PNG_BYTES = image_bytes("PNG")
SAMPLE_WEBP_BYTES = image_bytes("WEBP")


@pytest.mark.asyncio
async def test_missing_service_auth_rejected(mock_storage):
    """Calling internal storage endpoints without auth header must return 401."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-documents",
                files={"file": ("test.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "p123", "document_id": "d456"},
            )
            assert resp.status_code == 401
            assert "Authorization header is required" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_wrong_service_secret_rejected(mock_storage):
    """Calling internal storage endpoints with an invalid secret must return 401."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-documents",
                headers=INVALID_AUTH_HEADER,
                files={"file": ("test.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "p123", "document_id": "d456"},
            )
            assert resp.status_code == 401
            assert "Invalid service secret" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_valid_pdf_upload(mock_storage):
    """Uploading a valid PDF returns storage metadata and stores bytes."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("blood_test.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "patient-1", "document_id": "doc-999"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["storagePath"] == "medical-documents/patient-1/doc-999/blood_test.pdf"
            assert data["mimeType"] == "application/pdf"
            assert data["fileSize"] == len(SAMPLE_PDF_BYTES)

            # Verify file exists in storage
            assert await mock_storage.exists(data["storagePath"]) is True
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_valid_image_uploads(mock_storage):
    """Uploading JPEG, PNG, and WebP images succeeds with proper MIME classification."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. JPEG
            resp_jpg = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("xray.jpg", SAMPLE_JPEG_BYTES, "image/jpeg")},
                data={"patient_id": "pat_img", "document_id": "doc_jpg"},
            )
            assert resp_jpg.status_code == 201
            assert resp_jpg.json()["mimeType"] == "image/jpeg"

            # 2. PNG
            resp_png = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("scan.png", SAMPLE_PNG_BYTES, "image/png")},
                data={"patient_id": "pat_img", "document_id": "doc_png"},
            )
            assert resp_png.status_code == 201
            assert resp_png.json()["mimeType"] == "image/png"

            # 3. WebP
            resp_webp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("photo.webp", SAMPLE_WEBP_BYTES, "image/webp")},
                data={"patient_id": "pat_img", "document_id": "doc_webp"},
            )
            assert resp_webp.status_code == 201
            assert resp_webp.json()["mimeType"] == "image/webp"
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_unsupported_mime_rejected(mock_storage):
    """Files with unsupported content types or invalid magic bytes are rejected."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Plain text file
            resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("notes.txt", b"plain text is not allowed", "text/plain")},
                data={"patient_id": "p1", "document_id": "d1"},
            )
            assert resp.status_code == 400
            assert "Unsupported file format" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_oversized_file_rejected(mock_storage):
    """Files exceeding 10 MB are rejected with 413 Payload Too Large."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            large_bytes = b"%PDF-1.4" + b"0" * (10 * 1024 * 1024 + 10)
            resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("huge.pdf", large_bytes, "application/pdf")},
                data={"patient_id": "p1", "document_id": "d1"},
            )
            assert resp.status_code == 413
            assert "exceeds maximum allowed size" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_path_isolation_and_sanitization(mock_storage):
    """Ensures deterministic path construction and aggressive filename sanitization."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Filename with path traversal attempt and spaces
            malicious_filename = "../../../../../etc/passwd blood test report #1.pdf"
            resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": (malicious_filename, SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "patient_secure_42", "document_id": "doc_secure_88"},
            )
            assert resp.status_code == 201
            storage_path = resp.json()["storagePath"]

            # Path must start with medical-documents/
            assert storage_path.startswith("medical-documents/patient_secure_42/doc_secure_88/")
            # Must not contain traversal sequences
            assert ".." not in storage_path
            assert "etc/passwd" not in storage_path
            assert storage_path.endswith(".pdf")
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_identifier_traversal_rejected(mock_storage):
    """Path traversal in patient_id or document_id is rejected."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("test.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "../evil_patient", "document_id": "doc123"},
            )
            assert resp.status_code == 400
            assert "Invalid characters" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_signed_url_generation(mock_storage):
    """Tests generating a short-lived signed access URL."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Upload a document
            upload_resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("scan.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "p_sign", "document_id": "d_sign"},
            )
            assert upload_resp.status_code == 201
            storage_path = upload_resp.json()["storagePath"]

            # 2. Request signed URL without auth -> rejected
            unauth_resp = await client.get(
                "/internal/medical-documents/signed-url",
                params={"storagePath": storage_path},
            )
            assert unauth_resp.status_code == 401

            # 3. Request signed URL with auth -> succeeds
            sign_resp = await client.get(
                "/internal/medical-documents/signed-url",
                params={"storagePath": storage_path, "expiresIn": 600},
                headers=VALID_AUTH_HEADER,
            )
            assert sign_resp.status_code == 200
            res = sign_resp.json()
            assert "url" in res
            assert res["expiresIn"] == 600
            assert storage_path in res["url"]
    finally:
        app.dependency_overrides.pop(get_object_storage, None)


@pytest.mark.asyncio
async def test_delete_endpoint(mock_storage):
    """Tests deleting a medical document from object storage."""
    app.dependency_overrides[get_object_storage] = lambda: mock_storage
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Upload a document
            upload_resp = await client.post(
                "/internal/medical-documents",
                headers=VALID_AUTH_HEADER,
                files={"file": ("to_delete.pdf", SAMPLE_PDF_BYTES, "application/pdf")},
                data={"patient_id": "p_del", "document_id": "d_del"},
            )
            assert upload_resp.status_code == 201
            storage_path = upload_resp.json()["storagePath"]
            assert await mock_storage.exists(storage_path) is True

            # 2. Delete without auth -> rejected
            unauth_del = await client.delete(
                "/internal/medical-documents",
                params={"storagePath": storage_path},
            )
            assert unauth_del.status_code == 401

            # 3. Delete with auth -> succeeds
            del_resp = await client.delete(
                "/internal/medical-documents",
                params={"storagePath": storage_path},
                headers=VALID_AUTH_HEADER,
            )
            assert del_resp.status_code == 200
            assert del_resp.json()["success"] is True

            # Verify file is deleted in storage
            assert await mock_storage.exists(storage_path) is False
    finally:
        app.dependency_overrides.pop(get_object_storage, None)
