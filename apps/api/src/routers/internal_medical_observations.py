from src.security.medical_context import get_medical_context, bind_patient, validate_storage_scope, patient_collection_id
from src.security.service_context import ServiceContext
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.routers.internal_medical_documents import sanitize_identifier
from src.security.service_auth import verify_service_secret
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
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
) -> MedicalObservationQueryResponse:
    clean_patient_id = sanitize_identifier(body.patient_id, "patient_id")
    _auth = bind_patient(_auth, clean_patient_id)

    total_count = await obs_service.count_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=body.observation_types,
        from_date=body.from_date,
        to_date=body.to_date,
    )

    observations = await obs_service.query_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=body.observation_types,
        from_date=body.from_date,
        to_date=body.to_date,
        limit=body.limit,
        offset=body.offset,
        sort_order=body.sort,
    )

    items = [obs_service.model_to_item(obs) for obs in observations]
    has_more = (body.offset + len(items)) < total_count

    logger.info(
        "Medical observations queried",
        patient_id=clean_patient_id,
        count=len(items),
        total_count=total_count,
        has_more=has_more,
        offset=body.offset,
        limit=body.limit,
        sort=body.sort,
    )

    return MedicalObservationQueryResponse(
        observations=items,
        total_count=total_count,
        has_more=has_more,
        offset=body.offset,
        limit=body.limit,
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: str = Query("desc", pattern="^(asc|desc)$"),
    _auth: ServiceContext = Depends(get_medical_context),
    session: AsyncSession = Depends(get_db),
    obs_service: MedicalObservationService = Depends(get_medical_observation_service),
) -> MedicalObservationQueryResponse:
    clean_patient_id = sanitize_identifier(patient_id, "patient_id")
    _auth = bind_patient(_auth, clean_patient_id)
    types = [observation_type] if observation_type else None

    total_count = await obs_service.count_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=types,
    )

    observations = await obs_service.query_observations(
        session=session,
        patient_id=clean_patient_id,
        observation_types=types,
        limit=limit,
        offset=offset,
        sort_order=sort,
    )

    items = [obs_service.model_to_item(obs) for obs in observations]
    has_more = (offset + len(items)) < total_count

    return MedicalObservationQueryResponse(
        observations=items,
        total_count=total_count,
        has_more=has_more,
        offset=offset,
        limit=limit,
    )
