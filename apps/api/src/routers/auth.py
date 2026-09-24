import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import get_db
from src.models.user import User
from src.schemas.auth import (
    AuthTokenResponse,
    OAuthExchangeRequest,
    OAuthUrlResponse,
    UserLoginRequest,
    UserResponse,
    UserSignupRequest,
)
from src.security.jwt import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from src.services.oauth import OAuthService

logger = structlog.get_logger()
router = APIRouter(prefix="/auth", tags=["Authentication"])


def get_default_redirect_uri(request: Request, provider: str) -> str:
    """Derive backend OAuth callback URL if not explicitly set."""
    if provider == "google" and settings.GOOGLE_REDIRECT_URI:
        return settings.GOOGLE_REDIRECT_URI
    if provider == "github" and settings.GITHUB_REDIRECT_URI:
        return settings.GITHUB_REDIRECT_URI

    # Fallback to current request's base URL
    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/{provider}/callback"


@router.post(
    "/signup", response_model=AuthTokenResponse, status_code=status.HTTP_201_CREATED
)
async def signup(payload: UserSignupRequest, db: AsyncSession = Depends(get_db)):
    """Register a new user account with email and password."""
    email_clean = payload.email.lower().strip()

    # Check for existing email
    stmt = select(User).where(User.email == email_clean)
    existing_user = (await db.execute(stmt)).scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An account with this email address already exists. Please log in.",
        )

    # Hash password & persist user
    new_user = User(
        id=str(uuid.uuid4()),
        email=email_clean,
        name=payload.name.strip() if payload.name else None,
        hashed_password=hash_password(payload.password),
        auth_provider="local",
        is_active=True,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Generate JWT token
    token, expires_in = create_access_token(
        data={"sub": new_user.id, "email": new_user.email}
    )

    logger.info("New user registered", user_id=new_user.id, email=new_user.email)
    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserResponse.model_validate(new_user),
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(payload: UserLoginRequest, db: AsyncSession = Depends(get_db)):
    """Authenticate with email and password and return a JWT access token."""
    email_clean = payload.email.lower().strip()

    stmt = select(User).where(User.email == email_clean)
    user = (await db.execute(stmt)).scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No account found with this email address.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"This account was registered using {user.auth_provider}. Please sign in using {user.auth_provider}.",
        )

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password. Please try again.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated.",
        )

    token, expires_in = create_access_token(data={"sub": user.id, "email": user.email})

    logger.info("User logged in successfully", user_id=user.id, email=user.email)
    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_my_profile(current_user: User = Depends(get_current_user)):
    """Retrieve the profile of the currently authenticated user."""
    return UserResponse.model_validate(current_user)


# ==============================================================================
# Google OAuth Endpoints
# ==============================================================================


@router.get("/google/url", response_model=OAuthUrlResponse)
async def get_google_auth_url(request: Request, redirect_uri: str | None = None):
    """Retrieve the Google OAuth consent URL for client redirection."""
    target_redirect = redirect_uri or get_default_redirect_uri(request, "google")
    url = OAuthService.get_google_auth_url(redirect_uri=target_redirect)
    return OAuthUrlResponse(provider="google", url=url)


@router.get("/google/login")
async def google_login_redirect(request: Request):
    """Redirect browser directly to Google OAuth consent page or frontend error."""
    if not settings.GOOGLE_CLIENT_ID:
        error_msg = "Google OAuth is not configured on server. Please set GOOGLE_CLIENT_ID in .env"
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?error={error_msg}&provider=google"
        )
    target_redirect = get_default_redirect_uri(request, "google")
    url = OAuthService.get_google_auth_url(redirect_uri=target_redirect)
    return RedirectResponse(url=url)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback, exchange authorization code, and issue JWT."""
    if error:
        logger.warning("Google OAuth callback error", error=error)
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?error={error}"
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization code was not provided in callback query parameters.",
        )

    target_redirect = get_default_redirect_uri(request, "google")
    user_info = await OAuthService.exchange_google_code(
        code=code, redirect_uri=target_redirect
    )

    user = await OAuthService.get_or_create_oauth_user(
        db=db,
        provider="google",
        provider_id=user_info["provider_id"],
        email=user_info["email"],
        name=user_info.get("name"),
        avatar_url=user_info.get("avatar_url"),
    )

    token, _ = create_access_token(data={"sub": user.id, "email": user.email})

    # Redirect to Next.js frontend with the JWT token
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={token}&provider=google"
    )


# ==============================================================================
# GitHub OAuth Endpoints
# ==============================================================================


@router.get("/github/url", response_model=OAuthUrlResponse)
async def get_github_auth_url(request: Request, redirect_uri: str | None = None):
    """Retrieve the GitHub OAuth consent URL for client redirection."""
    target_redirect = redirect_uri or get_default_redirect_uri(request, "github")
    url = OAuthService.get_github_auth_url(redirect_uri=target_redirect)
    return OAuthUrlResponse(provider="github", url=url)


@router.get("/github/login")
async def github_login_redirect(request: Request):
    """Redirect browser directly to GitHub OAuth consent page or frontend error."""
    if not settings.GITHUB_CLIENT_ID:
        error_msg = "GitHub OAuth is not configured on server. Please set GITHUB_CLIENT_ID in .env"
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?error={error_msg}&provider=github"
        )
    target_redirect = get_default_redirect_uri(request, "github")
    url = OAuthService.get_github_auth_url(redirect_uri=target_redirect)
    return RedirectResponse(url=url)


@router.get("/github/callback")
async def github_callback(
    request: Request,
    code: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle GitHub OAuth callback, exchange authorization code, and issue JWT."""
    if error:
        logger.warning("GitHub OAuth callback error", error=error)
        return RedirectResponse(
            url=f"{settings.FRONTEND_URL}/auth/callback?error={error}"
        )

    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Authorization code was not provided in callback query parameters.",
        )

    target_redirect = get_default_redirect_uri(request, "github")
    user_info = await OAuthService.exchange_github_code(
        code=code, redirect_uri=target_redirect
    )

    user = await OAuthService.get_or_create_oauth_user(
        db=db,
        provider="github",
        provider_id=user_info["provider_id"],
        email=user_info["email"],
        name=user_info.get("name"),
        avatar_url=user_info.get("avatar_url"),
    )

    token, _ = create_access_token(data={"sub": user.id, "email": user.email})

    # Redirect to Next.js frontend with the JWT token
    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={token}&provider=github"
    )


# ==============================================================================
# SPA Direct OAuth Token Exchange
# ==============================================================================


@router.post("/oauth-exchange", response_model=AuthTokenResponse)
async def oauth_exchange(
    request: Request,
    payload: OAuthExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Allows SPAs (Single Page Applications) to exchange OAuth codes or Google ID tokens directly."""
    if payload.provider == "google":
        if payload.credential:
            user_info = await OAuthService.verify_google_credential(payload.credential)
        elif payload.code:
            target_redirect = payload.redirect_uri or get_default_redirect_uri(
                request, "google"
            )
            user_info = await OAuthService.exchange_google_code(
                payload.code, target_redirect
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'credential' or 'code' is required for Google OAuth.",
            )
    elif payload.provider == "github":
        if not payload.code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'code' is required for GitHub OAuth exchange.",
            )
        target_redirect = payload.redirect_uri or get_default_redirect_uri(
            request, "github"
        )
        user_info = await OAuthService.exchange_github_code(
            payload.code, target_redirect
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported OAuth provider: {payload.provider}",
        )

    user = await OAuthService.get_or_create_oauth_user(
        db=db,
        provider=payload.provider,
        provider_id=user_info["provider_id"],
        email=user_info["email"],
        name=user_info.get("name"),
        avatar_url=user_info.get("avatar_url"),
    )

    token, expires_in = create_access_token(data={"sub": user.id, "email": user.email})

    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
        user=UserResponse.model_validate(user),
    )


@router.get("/dev-login")
async def dev_login(
    provider: str = Query("google"),
    db: AsyncSession = Depends(get_db),
):
    """Development testing shortcut to simulate Google or GitHub OAuth callback."""
    if settings.ENVIRONMENT != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev login is only permitted in development mode.",
        )

    provider_name = (
        provider.lower() if provider.lower() in ["google", "github"] else "google"
    )
    mock_id = f"dev_{provider_name}_998877"
    mock_email = f"demo.{provider_name}@searchsphere.io"
    mock_name = f"Demo {provider_name.capitalize()} Developer"
    mock_avatar = f"https://api.dicebear.com/7.x/bottts/svg?seed={provider_name}"

    user = await OAuthService.get_or_create_oauth_user(
        db=db,
        provider=provider_name,
        provider_id=mock_id,
        email=mock_email,
        name=mock_name,
        avatar_url=mock_avatar,
    )

    token, _ = create_access_token(data={"sub": user.id, "email": user.email})

    return RedirectResponse(
        url=f"{settings.FRONTEND_URL}/auth/callback?token={token}&provider={provider_name}"
    )
