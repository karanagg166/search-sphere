from pydantic import BaseModel, Field, field_validator


class ConversationCreate(BaseModel):
    external_reference: str | None = Field(default=None, min_length=1, max_length=255)
    collection_id: str | None = Field(default=None, min_length=1, max_length=128)
    owner_subject_id: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("external_reference", "collection_id", "owner_subject_id")
    @classmethod
    def clean_identifier(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Identifier cannot be whitespace.")
        return value.strip() if value is not None else None


class ConversationResponse(BaseModel):
    id: str
    external_reference: str
    client_id: str
    tenant_id: str
    actor_id: str
    collection_id: str | None
    owner_subject_id: str | None
