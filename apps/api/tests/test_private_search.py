import uuid

import pymupdf
import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.storage.object_storage import LocalStorage, get_object_storage


def public_pdf():
    with pymupdf.open() as document:
        document.new_page().insert_text((50, 70), "PUBLIC SYNTHETIC PRIVATE-TEXT-59 verification code.")
        return document.tobytes()


@pytest.mark.asyncio
async def test_native_private_search_http_is_owned_and_never_requires_ai_approval(tmp_path):
    storage = LocalStorage(str(tmp_path), "synthetic-private-test")
    app.dependency_overrides[get_object_storage] = lambda: storage
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/processing-options")).status_code == 401
            assert (await client.post("/private-search", json={"document_id": "unknown", "query": "PUBLIC"})).status_code == 401
            headers = []
            for _ in range(2):
                signup = await client.post("/auth/signup", json={"name": "Synthetic private search", "email": str(uuid.uuid4()) + "@example.com", "password": "SyntheticPrivatePass-123!"})
                assert signup.status_code == 201
                headers.append({"Authorization": "Bearer " + signup.json()["access_token"]})
            options = await client.get("/processing-options", headers=headers[0])
            assert options.status_code == 200
            assert options.json()["ai_answers_available"] is False
            assert options.json()["private_text_search_available"] is True
            upload = await client.post("/documents", headers=headers[0], files={"file": ("public-synthetic.pdf", public_pdf(), "application/pdf")})
            assert upload.status_code == 201
            document_id = upload.json()["id"]
            request = {"document_id": document_id, "query": "PRIVATE-TEXT-59"}
            result = await client.post("/private-search", headers=headers[0], json=request)
            assert result.status_code == 200
            assert result.json()["external_provider_used"] is False
            assert result.json()["generated_answer"] is False
            assert result.json()["results"][0]["document_id"] == document_id
            assert result.json()["results"][0]["page_number"] == 1
            assert (await client.post("/private-search", headers=headers[1], json=request)).status_code == 404
            assert (await client.delete("/documents/" + document_id, headers=headers[0])).status_code == 204
            assert (await client.post("/private-search", headers=headers[0], json=request)).status_code == 404
    finally:
        app.dependency_overrides.clear()
