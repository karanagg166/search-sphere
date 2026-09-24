import uuid
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.user import User

logger = structlog.get_logger()


class OAuthService:
    @staticmethod
    def get_google_auth_url(redirect_uri: str, state: str | None = None) -> str:
        """Construct the Google OAuth2 authorization URL."""
        if not settings.GOOGLE_CLIENT_ID:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth is not configured on the server (missing GOOGLE_CLIENT_ID).",
            )
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    @staticmethod
    def get_github_auth_url(redirect_uri: str, state: str | None = None) -> str:
        """Construct the GitHub OAuth authorization URL."""
        if not settings.GITHUB_CLIENT_ID:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GitHub OAuth is not configured on the server (missing GITHUB_CLIENT_ID).",
            )
        params = {
            "client_id": settings.GITHUB_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "read:user user:email",
        }
        if state:
            params["state"] = state
        return f"https://github.com/login/oauth/authorize?{urlencode(params)}"

    @staticmethod
    async def exchange_google_code(code: str, redirect_uri: str) -> dict[str, Any]:
        """Exchange Google authorization code for user info."""
        if not settings.GOOGLE_CLIENT_ID or not settings.GOOGLE_CLIENT_SECRET:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Google OAuth credentials are missing in server configuration.",
            )

        token_url = "https://oauth2.googleapis.com/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                token_url,
                data={
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
            )

        if token_resp.status_code != 200:
            logger.error("Google token exchange failed", body=token_resp.text)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Google token exchange failed: {token_resp.text}",
            )

        tokens = token_resp.json()
        access_token = tokens.get("access_token")

        # Fetch profile
        async with httpx.AsyncClient(timeout=10.0) as client:
            userinfo_resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

        if userinfo_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve profile information from Google.",
            )

        data = userinfo_resp.json()
        return {
            "provider_id": data.get("sub"),
            "email": data.get("email"),
            "name": data.get("name"),
            "avatar_url": data.get("picture"),
        }

    @staticmethod
    async def verify_google_credential(credential: str) -> dict[str, Any]:
        """Verify Google ID token (from Google One Tap or GIS button)."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://oauth2.googleapis.com/tokeninfo?id_token={credential}"
            )

        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Google credential ID token.",
            )

        data = resp.json()
        return {
            "provider_id": data.get("sub"),
            "email": data.get("email"),
            "name": data.get("name"),
            "avatar_url": data.get("picture"),
        }

    @staticmethod
    async def exchange_github_code(
        code: str, redirect_uri: str | None = None
    ) -> dict[str, Any]:
        """Exchange GitHub authorization code for user info."""
        if not settings.GITHUB_CLIENT_ID or not settings.GITHUB_CLIENT_SECRET:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="GitHub OAuth credentials are missing in server configuration.",
            )

        token_url = "https://github.com/login/oauth/access_token"
        payload: dict[str, Any] = {
            "client_id": settings.GITHUB_CLIENT_ID,
            "client_secret": settings.GITHUB_CLIENT_SECRET,
            "code": code,
        }
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri

        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                token_url,
                json=payload,
                headers={"Accept": "application/json"},
            )

        if token_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub token exchange failed: {token_resp.text}",
            )

        tokens = token_resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            error_desc = tokens.get(
                "error_description", tokens.get("error", "Unknown error")
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"GitHub authentication error: {error_desc}",
            )

        # Fetch profile
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "SearchSphereApp",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            profile_resp = await client.get(
                "https://api.github.com/user", headers=headers
            )

        if profile_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to retrieve profile information from GitHub.",
            )

        profile = profile_resp.json()
        email = profile.get("email")

        # If email is private on GitHub profile, query the user/emails endpoint
        if not email:
            async with httpx.AsyncClient(timeout=10.0) as client:
                emails_resp = await client.get(
                    "https://api.github.com/user/emails", headers=headers
                )
            if emails_resp.status_code == 200:
                emails_list = emails_resp.json()
                for em in emails_list:
                    if em.get("primary") and em.get("verified"):
                        email = em.get("email")
                        break
                if not email and emails_list:
                    email = emails_list[0].get("email")

        if not email:
            email = f"{profile.get('login')}@users.noreply.github.com"

        return {
            "provider_id": str(profile.get("id")),
            "email": email,
            "name": profile.get("name") or profile.get("login"),
            "avatar_url": profile.get("avatar_url"),
        }

    @staticmethod
    async def get_or_create_oauth_user(
        db: AsyncSession,
        provider: str,
        provider_id: str,
        email: str,
        name: str | None = None,
        avatar_url: str | None = None,
    ) -> User:
        """Find existing user by provider_id or email, or create a new user."""
        # 1. Check by provider and provider_id
        stmt = select(User).where(
            User.auth_provider == provider,
            User.provider_id == provider_id,
        )
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()

        if user:
            # Update avatar or name if newly provided
            changed = False
            if avatar_url and not user.avatar_url:
                user.avatar_url = avatar_url
                changed = True
            if name and not user.name:
                user.name = name
                changed = True
            if changed:
                await db.commit()
                await db.refresh(user)
            return user

        # 2. Check by email
        stmt_email = select(User).where(User.email == email.lower())
        res_email = await db.execute(stmt_email)
        user_by_email = res_email.scalar_one_or_none()

        if user_by_email:
            # Link existing account with this provider
            if not user_by_email.provider_id:
                user_by_email.provider_id = provider_id
                user_by_email.auth_provider = provider
            if avatar_url and not user_by_email.avatar_url:
                user_by_email.avatar_url = avatar_url
            if name and not user_by_email.name:
                user_by_email.name = name
            await db.commit()
            await db.refresh(user_by_email)
            return user_by_email

        # 3. Create a new user
        new_user = User(
            id=str(uuid.uuid4()),
            email=email.lower(),
            name=name,
            avatar_url=avatar_url,
            auth_provider=provider,
            provider_id=provider_id,
            is_active=True,
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user
