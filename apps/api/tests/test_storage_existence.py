"""Only an explicit missing-object response proves private storage absence."""

import httpx
import pytest

from src.storage.object_storage import SupabaseStorage


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
async def test_storage_errors_never_mean_absent(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request.method)
        return httpx.Response(status)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("src.storage.object_storage.httpx.AsyncClient", lambda **kwargs: client)
    storage = SupabaseStorage(base_url="https://synthetic.invalid", api_key="synthetic-test-only")
    with pytest.raises(RuntimeError, match="existence check unavailable"):
        await storage.exists("public-synthetic-fixture.pdf")
    assert calls == ["HEAD"]


@pytest.mark.asyncio
@pytest.mark.parametrize("head_status,get_status,expected", [(200, None, True), (404, None, False), (405, 206, True), (501, 404, False)])
async def test_storage_explicit_head_and_range_results(monkeypatch, head_status, get_status, expected):
    def handler(request):
        if request.method == "HEAD":
            return httpx.Response(head_status)
        assert request.headers["Range"] == "bytes=0-0"
        return httpx.Response(get_status, content=b"x")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("src.storage.object_storage.httpx.AsyncClient", lambda **kwargs: client)
    storage = SupabaseStorage(base_url="https://synthetic.invalid", api_key="synthetic-test-only")
    assert await storage.exists("public-synthetic-fixture.pdf") is expected


@pytest.mark.asyncio
async def test_storage_range_error_is_not_absence(monkeypatch):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(405 if request.method == "HEAD" else 503)))
    monkeypatch.setattr("src.storage.object_storage.httpx.AsyncClient", lambda **kwargs: client)
    with pytest.raises(RuntimeError, match="existence check unavailable"):
        await SupabaseStorage(base_url="https://synthetic.invalid", api_key="synthetic-test-only").exists("fixture.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,missing", [({"statusCode":"404","error":"not_found","message":"Object not found"},True),
    ({"statusCode":"403","error":"not_found"},False),({"statusCode":"404","error":"AccessDenied"},False),({},False)])
async def test_legacy_supabase_missing_envelope_is_verified_with_bounded_range_get(monkeypatch,payload,missing):
    def handler(request):
        return httpx.Response(400) if request.method=="HEAD" else httpx.Response(400,json=payload)
    client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr("src.storage.object_storage.httpx.AsyncClient",lambda **kwargs:client)
    storage=SupabaseStorage(base_url="https://synthetic.invalid",api_key="synthetic-test-only")
    if missing:
        assert await storage.exists("fixture.pdf") is False
    else:
        with pytest.raises(RuntimeError,match="existence check unavailable"):
            await storage.exists("fixture.pdf")
