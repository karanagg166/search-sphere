import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.routers.internal_medical_documents import (
    sanitize_identifier,
    verify_service_secret,
)
from src.schemas.internal_medical_observations import (
    MedicalObservationQueryRequest,
    MedicalObservationQueryResponse,
)
from src.services.medical_observation_service import (
    MedicalObservationService,
    get_medical_observation_service,
)

logger = structlog.get_logger()

router = APIRouter(
    prefix="/internal/medical-observations",
    tags=["Internal Medical Observations"],
)


@router.post(
    "/query",
    response_model=MedicalObservationQueryResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Query patient medical observations with optional type and time filters",
)
async def query_patient_observations(
    body: MedicalObservationQueryRequest,
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
) -> MedicalObservationQueryResponse:
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")

    observations = await obs_service.query_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=body.observation_types,
        from_date=body.from_date,
        to_date=body.to_date,
        limit=body.limit,
        sort_order=body.sort,
    )

    items = [obs_service.model_to_item(obs) for obs in observations]

    logger.info(
        "Medical observations queried",
        patient_id=clean_patient_id,
        count=len(items),
        sort=body.sort,
    )

    return MedicalObservationQueryResponse(
        observations=items,
        total_count=len(items),
    )


@router.get(
    "",
    response_model=MedicalObservationQueryResponse,
    response_model_by_alias=True,
    status_code=status.HTTP_200_OK,
    summary="Query patient medical observations via GET",
)
async def get_patient_observations(
    patient_id: str = Query(..., alias="patientId", min_length=1, max_length=128),
    observation_type: str | None = Query(None, alias="observationType"),
    limit: int = Query(100, ge=1, le=500),
    sort: str = Query("asc", pattern="^(asc|desc)$"),
    _auth: bool = Depends(verify_service_secret),
    session: AsyncSession = Depends(get_db),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
) -> MedicalObservationQueryResponse:
    clean_patient_id = sanitize_identifier(patient_id, "patient_id")
    types = [observation_type] if observation_type else None

    observations = await obs_service.query_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=types,
        limit=limit,
        sort_order=sort,
    )

    items = [obs_service.model_to_item(obs) for obs in observations]
    return MedicalObservationQueryResponse(
        observations=items,
        total_count=len(items),
    )
