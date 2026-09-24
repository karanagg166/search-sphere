from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    storage_key: str
    file_url: str
    file_size: int
    mime_type: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]
