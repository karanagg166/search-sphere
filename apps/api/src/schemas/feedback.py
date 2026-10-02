from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeedbackCreate(BaseModel):
    """Payload to rate or provide feedback for a generated answer."""

    rating: Literal[1, -1] = Field(
        ...,
        description="Rating score: 1 for positive (thumbs up), -1 for negative (thumbs down).",
    )
    comment: str | None = Field(
        default=None,
        description="Optional qualitative comment or reason for the rating.",
        max_length=2000,
    )
    conversation_id: str | None = Field(
        default=None,
        description="Optional ID of associated conversation.",
    )
    message_id: str | None = Field(
        default=None,
        description="Optional ID of associated message.",
    )

    @field_validator("comment")
    @classmethod
    def clean_comment(cls, v: str | None) -> str | None:
        if v is not None:
            v_clean = v.strip()
            return v_clean if v_clean else None
        return None


class FeedbackResponse(BaseModel):
    """API response model for user feedback."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    conversation_id: str | None = None
    message_id: str | None = None
    rating: int
    comment: str | None = None
    created_at: datetime
