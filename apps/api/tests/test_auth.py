import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from src.main import app
from src.security.jwt import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hashing():
    raw = "SecurePassword123!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword!", hashed) is False


def test_jwt_token_creation_and_decoding():
    data = {"sub": "user-uuid-123", "email": "test@domain.com"}
    token, expires_in = create_access_token(data)
    assert isinstance(token, str)
    assert expires_in > 0

    decoded = decode_access_token(token)
    assert decoded["sub"] == "user-uuid-123"
    assert decoded["email"] == "test@domain.com"
    assert "exp" in decoded


@pytest.mark.asyncio
async def test_auth_workflow_and_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Health check
        health_resp = await client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"

        # 2. Signup with unique email
        unique_email = f"user_{uuid.uuid4().hex[:10]}@testsphere.io"
        signup_resp = await client.post(
            "/auth/signup",
            json={
                "name": "Integration User",
                "email": unique_email,
                "password": "supersecurepassword123",
            },
        )
        assert signup_resp.status_code == 201
        data = signup_resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["user"]["email"] == unique_email
        assert data["user"]["name"] == "Integration User"
        token = data["access_token"]

        # 3. Duplicate signup prevention
        dup_resp = await client.post(
            "/auth/signup",
            json={
                "name": "Duplicate User",
                "email": unique_email,
                "password": "anotherpassword",
            },
        )
        assert dup_resp.status_code == 400

        # 4. Login with correct password
        login_resp = await client.post(
            "/auth/login",
            json={
                "email": unique_email,
                "password": "supersecurepassword123",
            },
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert "access_token" in login_data
        assert login_data["user"]["email"] == unique_email

        # 5. Login with incorrect password
        bad_login_resp = await client.post(
            "/auth/login",
            json={
                "email": unique_email,
                "password": "wrongpassword",
            },
        )
        assert bad_login_resp.status_code == 401
        assert "Incorrect password" in bad_login_resp.json()["detail"]

        # 5b. Login with non-existent email
        no_email_resp = await client.post(
            "/auth/login",
            json={
                "email": "nonexistent_user_9999@testsphere.io",
                "password": "somepassword123",
            },
        )
        assert no_email_resp.status_code == 401
        assert "No account found" in no_email_resp.json()["detail"]

        # 6. Access /auth/me with valid Bearer token
        me_resp = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == unique_email

        # 7. Access /auth/me without token
        unauth_resp = await client.get("/auth/me")
        assert unauth_resp.status_code == 401

        # 8. Test Google auth URL endpoint
        google_url_resp = await client.get("/auth/google/url")
        assert google_url_resp.status_code == 200
        google_data = google_url_resp.json()
        assert "url" in google_data
        assert "accounts.google.com" in google_data["url"]
        assert (
            "http%3A%2F%2Flocalhost%3A3000%2Fapi%2Fauth%2Fcallback%2Fgoogle"
            in google_data["url"]
        )
