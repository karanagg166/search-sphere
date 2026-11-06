from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.schemas.ingestion import IngestionProgress


class DocumentResponse(BaseModel):
    id: str
    user_id: str
    filename: str
    storage_key: str
    file_url: str
    file_size: int
    mime_type: str
    status: str
    chunk_count: int | None = None
    current_version_id: str | None = None
    job: IngestionProgress | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    total: int
    documents: list[DocumentResponse]
