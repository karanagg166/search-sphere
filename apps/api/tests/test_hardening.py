import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from src.config import Settings
from src.main import app
from src.security.rate_limiter import InMemoryRateLimiter


@pytest.mark.asyncio
async def test_request_id_generated_when_missing():
    """Verify that an X-Request-ID header is generated and returned if client omits it."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        assert len(response.headers["X-Request-ID"]) > 0


@pytest.mark.asyncio
async def test_request_id_preserved_when_provided():
    """Verify that an incoming X-Request-ID header is preserved and echoed back."""
    custom_req_id = str(uuid.uuid4())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/health", headers={"X-Request-ID": custom_req_id})
        assert response.status_code == 200
        assert response.headers.get("X-Request-ID") == custom_req_id


@pytest.mark.asyncio
async def test_readiness_probe_success():
    """Verify that /ready probe reports database connectivity successfully."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        response = await ac.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"


def test_rate_limiter_logic():
    """Test unit logic of sliding-window InMemoryRateLimiter."""
    limiter = InMemoryRateLimiter(requests_per_minute=3, enabled=True)
    key = "test_client_key"

    # First 3 requests allowed
    allowed_1, rem_1, _ = limiter.is_allowed(key)
    assert allowed_1 is True
    assert rem_1 == 2

    allowed_2, rem_2, _ = limiter.is_allowed(key)
    assert allowed_2 is True
    assert rem_2 == 1

    allowed_3, rem_3, _ = limiter.is_allowed(key)
    assert allowed_3 is True
    assert rem_3 == 0

    # 4th request blocked
    allowed_4, rem_4, retry_after = limiter.is_allowed(key)
    assert allowed_4 is False
    assert rem_4 == 0
    assert retry_after > 0


async def create_user(client: AsyncClient, name: str = "HardeningUser") -> tuple[str, str, dict[str, str]]:
    email = f"{name.lower()}_{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post(
        "/auth/signup",
        json={"name": name, "email": email, "password": "Password123!"},
    )
    assert resp.status_code == 201
    data = resp.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    return user_id, token, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_rate_limiter_integration_blocks_excessive_requests():
    """Verify endpoint returns 429 when rate limit is exceeded."""
    from src.security.rate_limiter import rate_limiter

    # Temporarily set limit to 2 requests
    original_limit = rate_limiter.requests_per_minute
    rate_limiter.requests_per_minute = 2
    rate_limiter._history.clear()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as ac:
            _, _, headers = await create_user(ac, "RateLimitUser")

            # Request 1: allowed
            r1 = await ac.post("/search", json={"query": "test 1"}, headers=headers)
            assert r1.status_code != 429

            # Request 2: allowed
            r2 = await ac.post("/search", json={"query": "test 2"}, headers=headers)
            assert r2.status_code != 429

            # Request 3: blocked with 429 Too Many Requests
            r3 = await ac.post("/search", json={"query": "test 3"}, headers=headers)
            assert r3.status_code == 429
            assert "Retry-After" in r3.headers
            assert "Too many requests" in r3.json()["detail"]
    finally:
        rate_limiter.requests_per_minute = original_limit
        rate_limiter._history.clear()


@pytest.mark.asyncio
async def test_query_length_limit_exceeded():
    """Verify input validation rejects queries exceeding MAX_QUERY_LENGTH."""
    oversized_query = "a" * 1001

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        _, _, headers = await create_user(ac, "OversizedUser")
        response = await ac.post(
            "/search", json={"query": oversized_query}, headers=headers
        )
        assert response.status_code == 422
        errors = response.json()["detail"]
        assert any("Query exceeds maximum allowed limit" in str(e) for e in errors)


def test_production_secrets_validation_fails_on_dev_defaults():
    """Verify that Settings validation fails when ENVIRONMENT=production but default JWT is used."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="search-sphere-super-secure-jwt-secret-key-production-ready-2026",
            COHERE_API_KEY="valid_cohere_key",
        )
    assert "JWT_SECRET_KEY must be explicitly set" in str(exc_info.value)


def test_production_secrets_validation_fails_on_missing_cohere_key():
    """Verify that Settings validation fails when ENVIRONMENT=production but COHERE_API_KEY is empty."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            JWT_SECRET_KEY="completely-custom-production-jwt-secret-key-xyz123",
            COHERE_API_KEY="",
        )
    assert "COHERE_API_KEY must be provided in production" in str(exc_info.value)


def test_production_secrets_validation_succeeds_with_secure_config():
    """Verify that Settings validation passes in production when secure credentials are provided."""
    s = Settings(
        ENVIRONMENT="production",
        JWT_SECRET_KEY="completely-custom-production-jwt-secret-key-xyz123",
        COHERE_API_KEY="actual_production_cohere_key",
    )
    assert s.ENVIRONMENT == "production"
    assert s.JWT_SECRET_KEY == "completely-custom-production-jwt-secret-key-xyz123"
