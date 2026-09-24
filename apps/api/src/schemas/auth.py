from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserSignupRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    email: EmailStr
    password: str = Field(..., min_length=6, max_length=128)


class UserLoginRequest(BaseModel):
    email: EmailStr
    password: str


class OAuthExchangeRequest(BaseModel):
    provider: Literal["google", "github"]
    code: str | None = None
    credential: str | None = None  # Google ID token (GIS)
    access_token: str | None = None
    redirect_uri: str | None = None


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    auth_provider: str
    is_active: bool
    created_at: datetime | None = None


class AuthTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class OAuthUrlResponse(BaseModel):
    provider: str
    url: str
