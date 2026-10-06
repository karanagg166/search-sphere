from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CollectionCreateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    collection_id: str = Field(..., min_length=1, max_length=128, description="Logical identifier for collection")
    name: str = Field(..., min_length=1, max_length=255, description="Collection name")
    description: str | None = Field(None, max_length=1000)
    metadata: dict[str, Any] | None = Field(None, description="Arbitrary custom collection metadata")


class CollectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: str
    client_id: str
    tenant_id: str
    collection_id: str
    name: str
    description: str | None = None
    metadata_json: dict[str, Any] | None = Field(None, serialization_alias="metadata")
    created_at: datetime
    updated_at: datetime


class CollectionListResponse(BaseModel):
    total: int
    collections: list[CollectionResponse]
