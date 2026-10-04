from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class MedicalObservationQueryRequest(BaseModel):
    """Request schema for querying patient medical observations."""

    model_config = ConfigDict(populate_by_name=True)

    patient_id: str = Field(..., alias="patientId", min_length=1, max_length=128)
    observation_types: list[str] | None = Field(default=None, alias="observationTypes")
    from_date: datetime | str | None = Field(default=None, alias="fromDate")
    to_date: datetime | str | None = Field(default=None, alias="toDate")
    limit: int = Field(default=100, ge=1, le=500)
    sort: str = Field(default="asc", pattern="^(asc|desc)$")


class MedicalObservationItem(BaseModel):
    """Sanitized medical observation DTO returned via internal API."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    type: str
    display_name: str = Field(..., alias="displayName", serialization_alias="displayName")
    value: float | None = None
    value_text: str | None = Field(default=None, alias="valueText", serialization_alias="valueText")
    secondary_value: float | None = Field(
        default=None, alias="secondaryValue", serialization_alias="secondaryValue"
    )
    unit: str | None = None
    observed_at: str | None = Field(default=None, alias="observedAt", serialization_alias="observedAt")
    reported_at: str | None = Field(default=None, alias="reportedAt", serialization_alias="reportedAt")
    is_date_inferred: bool = Field(
        default=False, alias="isDateInferred", serialization_alias="isDateInferred"
    )
    document_id: str = Field(..., alias="documentId", serialization_alias="documentId")
    page_number: int | None = Field(default=None, alias="pageNumber", serialization_alias="pageNumber")
    chunk_index: int | None = Field(default=None, alias="chunkIndex", serialization_alias="chunkIndex")
    confidence: float = 1.0


class MedicalObservationQueryResponse(BaseModel):
    """Response schema containing patient medical observations."""

    model_config = ConfigDict(populate_by_name=True)

    observations: list[MedicalObservationItem] = Field(default_factory=list)
    total_count: int = Field(default=0, alias="totalCount", serialization_alias="totalCount")
