import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.db import Base


class MedicalObservation(Base):
    """
    Structured medical observation extracted from external documents (e.g. vitals, lab values).

    Persisted in Search Sphere PostgreSQL, partitioned logically by patient and document.
    Does NOT connect to internal Search Sphere User accounts.
    """

    __tablename__ = "medical_observations"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )

    source_system: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="quick_clinic",
        index=True,
    )

    external_patient_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    external_document_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        index=True,
    )

    observation_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )

    display_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    value_numeric: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    value_text: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    value_secondary_numeric: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )

    unit: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    reported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    is_date_inferred: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    page_number: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    chunk_index: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    confidence: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=1.0,
    )

    extraction_method: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="REGEX",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_med_obs_src_pat_type",
            "source_system",
            "external_patient_id",
            "observation_type",
        ),
        Index(
            "ix_med_obs_src_pat_date",
            "source_system",
            "external_patient_id",
            "observed_at",
        ),
        Index(
            "ix_med_obs_src_doc",
            "source_system",
            "external_document_id",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<MedicalObservation id={self.id} "
            f"type={self.observation_type} "
            f"val={self.value_numeric} "
            f"unit={self.unit} "
            f"patient={self.external_patient_id}>"
        )
