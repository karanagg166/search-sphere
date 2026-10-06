from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ServiceClientCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    client_id: str = Field(..., min_length=2, max_length=64, description="Unique client identifier slug")
    name: str = Field(..., min_length=2, max_length=255, description="Human readable application name")
    allowed_scopes: list[str] = Field(
        default_factory=lambda: [
            "documents:read",
            "documents:write",
            "documents:delete",
            "search:execute",
            "answers:generate",
            "collections:manage",
        ],
        description="Allowed authorization scopes or ['*']",
    )
    authorized_tenants: list[str] = Field(
        default_factory=lambda: ["*"],
        description="List of tenant IDs this client may access, or ['*'] for all",
    )
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10000)


class ServiceClientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    client_id: str
    name: str
    api_key_prefix: str
    status: str
    allowed_scopes: list[str]
    authorized_tenants: list[str]
    rate_limit_per_minute: int
    created_at: datetime
    updated_at: datetime
    revoked_at: datetime | None = None


class ServiceClientCreatedResponse(ServiceClientResponse):
    api_key: str = Field(..., description="High-entropy raw API key. Revealed only once upon creation.")


class ServiceClientRotateResponse(BaseModel):
    client_id: str
    api_key: str = Field(..., description="Newly generated raw API key.")
    api_key_prefix: str
    message: str = "Credential successfully rotated. Please securely update client environment."
