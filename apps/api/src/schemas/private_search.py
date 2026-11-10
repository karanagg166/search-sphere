from pydantic import BaseModel, Field


class PrivateSearchRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=36)
    query: str = Field(min_length=1, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)
