from datetime import datetime
from typing import Sequence

import structlog
from sqlalchemy import Select, asc, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.medical_observation import MedicalObservation
from src.schemas.internal_medical_observations import MedicalObservationItem

logger = structlog.get_logger()


class MedicalObservationService:
    """
    Dedicated service for querying and managing patient-isolated structured medical observations.
    Encapsulates all SQL logic, sorting, filtering, and DTO transformations.
    """

    def _parse_datetime(self, val: datetime | str | None) -> datetime | None:
        if not val:
            return None
        if isinstance(val, datetime):
            return val
        try:
            clean_str = val.strip()
            if clean_str.endswith("Z"):
                clean_str = clean_str[:-1] + "+00:00"
            return datetime.fromisoformat(clean_str)
        except Exception:
            return None

    def model_to_item(self, obs: MedicalObservation) -> MedicalObservationItem:
        """Converts an ORM MedicalObservation model into a sanitized API DTO."""
        return MedicalObservationItem(
            id=obs.id,
            type=obs.observation_type,
            displayName=obs.display_name,
            value=obs.value_numeric,
            valueText=obs.value_text,
            secondaryValue=obs.value_secondary_numeric,
            unit=obs.unit,
            observedAt=obs.observed_at.isoformat() if obs.observed_at else None,
            reportedAt=obs.reported_at.isoformat() if obs.reported_at else None,
            isDateInferred=obs.is_date_inferred,
            documentId=obs.external_document_id,
            pageNumber=obs.page_number,
            chunkIndex=obs.chunk_index,
            confidence=obs.confidence,
        )

    def _build_query_stmt(
        self,
        patient_id: str,
        observation_types: list[str] | None = None,
        from_date: datetime | str | None = None,
        to_date: datetime | str | None = None,
    ) -> Select:
        stmt: Select = select(MedicalObservation).where(
            MedicalObservation.source_system == "quick_clinic",
            MedicalObservation.tenant_id == "quick_clinic_default",
            MedicalObservation.external_patient_id == patient_id,
        )

        if observation_types:
            clean_types = [t.strip().upper() for t in observation_types if t.strip()]
            if clean_types:
                stmt = stmt.where(MedicalObservation.observation_type.in_(clean_types))

        parsed_from = self._parse_datetime(from_date)
        if parsed_from:
            stmt = stmt.where(MedicalObservation.observed_at >= parsed_from)

        parsed_to = self._parse_datetime(to_date)
        if parsed_to:
            stmt = stmt.where(MedicalObservation.observed_at <= parsed_to)

        return stmt

    async def count_observations(
        self,
        session: AsyncSession,
        patient_id: str,
        observation_types: list[str] | None = None,
        from_date: datetime | str | None = None,
        to_date: datetime | str | None = None,
    ) -> int:
        """Counts matching observations for pagination total."""
        base_stmt = self._build_query_stmt(patient_id, observation_types, from_date, to_date)
        count_stmt = select(func.count()).select_from(base_stmt.subquery())
        res = await session.execute(count_stmt)
        return int(res.scalar_one() or 0)

    async def query_observations(
        self,
        session: AsyncSession,
        patient_id: str,
        observation_types: list[str] | None = None,
        from_date: datetime | str | None = None,
        to_date: datetime | str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort_order: str = "desc",
    ) -> list[MedicalObservation]:
        """
        Queries observations with strict server-side patient isolation.
        Supports filtering by observation type, date window, sorting, limit, and offset.
        Orders deterministically by observed_at and id.
        """
        stmt = self._build_query_stmt(patient_id, observation_types, from_date, to_date)

        if sort_order.lower() == "desc":
            stmt = stmt.order_by(
                desc(MedicalObservation.observed_at).nulls_last(),
                asc(MedicalObservation.id),
            )
        else:
            stmt = stmt.order_by(
                asc(MedicalObservation.observed_at).nulls_last(),
                asc(MedicalObservation.id),
            )

        resolved_limit = max(1, min(limit, 200))
        resolved_offset = max(0, offset)
        stmt = stmt.offset(resolved_offset).limit(resolved_limit)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_observations(
        self,
        session: AsyncSession,
        patient_id: str,
        observation_types: list[str] | None = None,
        limit: int = 1,
    ) -> list[MedicalObservation]:
        """
        Retrieves the most recent observation(s) ordered strictly by observed_at DESC.
        """
        return await self.query_observations(
            session=session,
            patient_id=patient_id,
            observation_types=observation_types,
            sort_order="desc",
            limit=limit,
        )


_observation_service = MedicalObservationService()


def get_medical_observation_service() -> MedicalObservationService:
    return _observation_service
