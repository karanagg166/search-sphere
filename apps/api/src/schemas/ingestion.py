from datetime import datetime

from pydantic import BaseModel


class IngestionProgress(BaseModel):
    status: str
    stage: str
    attempts: int
    generation: int = 1
    max_attempts: int = 3
    queue_age_seconds: float
    queued_at: datetime
    updated_at: datetime
    error_code: str | None = None
