"""Explicit private text mode, separate from AI answers and conversation history."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.db import get_db
from src.inference.data_policy import classification_for_scope
from src.inference.policy import ProviderNotApprovedError, check_cohere_configuration
from src.models.user import User
from src.schemas.private_search import PrivateSearchRequest
from src.security.jwt import get_current_user
from src.security.rate_limiter import rate_limiter
from src.services.private_text_search import private_text_search
from src.storage.object_storage import ObjectStorage, get_object_storage

router = APIRouter(tags=["Private text search"])


@router.get("/processing-options")
async def processing_options(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    classification = await classification_for_scope(db, "search_sphere", user.id, None, user.id)
    try:
        check_cohere_configuration(settings.COHERE_API_KEY or "", classification)
        allowed = True
    except ProviderNotApprovedError:
        allowed = False
    return {"ai_answers_available": allowed, "private_text_search_available": True,
            "classification": classification.value,
            "reason_code": None if allowed else "provider_not_approved",
            "provider": "cohere_trial"}


@router.post("/private-search", dependencies=[Depends(rate_limiter.check)])
async def search_private_text(
    body: PrivateSearchRequest,
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db),
    storage: ObjectStorage = Depends(get_object_storage),
):
    return await private_text_search(db, storage, body.document_id, user.id, body.query, body.limit)
