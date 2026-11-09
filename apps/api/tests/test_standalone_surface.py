"""Standalone production never mounts the archived client adapters."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path"), [
    ("GET", "/api/v1/clients/me"),
    ("POST", "/api/v1/documents/upload"),
    ("POST", "/api/v1/answers"),
    ("POST", "/api/v1/conversations"),
    ("POST", "/internal/medical-documents"),
    ("POST", "/internal/medical-rag/chat"),
    ("POST", "/internal/medical-rag/chat/stream"),
])
async def test_consumer_routes_are_unavailable_even_with_delegated_headers(method, path):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.request(method, path, headers={
            "Authorization": "Bearer test-service-secret", "X-Tenant-ID": "synthetic-school",
            "X-Subject-ID": "synthetic-patient", "X-Actor-ID": "synthetic-teacher",
        })
    assert response.status_code == 404


def test_openapi_advertises_native_features_only():
    paths = app.openapi()["paths"]
    assert {"/documents", "/search", "/answer", "/conversations", "/auth/login"} <= paths.keys()
    assert not any(path.startswith(("/api/v1", "/internal/medical")) for path in paths)
