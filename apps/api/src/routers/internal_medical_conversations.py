"""Scoped medical compatibility deletion; never enters inference dependencies."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_db
from src.security.medical_context import bind_patient, get_medical_context
from src.security.service_context import ServiceContext
from src.services.delegated_conversations import remove_conversation_reference

router = APIRouter(prefix="/internal/medical-rag/conversations", tags=["Internal Medical Conversations"])


@router.delete("/{reference}")
async def remove(
    reference: Annotated[str, Path(min_length=1, max_length=255)],
    db: AsyncSession = Depends(get_db),
    context: ServiceContext = Depends(get_medical_context),
):
    # The legacy adapter already grants these two capabilities. Requiring both
    # avoids granting generic conversation administration to a document-only key.
    context.require_scope("answers:generate")
    if not context.subject_id or not context.actor_id or not reference.strip():
        raise HTTPException(status_code=422, detail="Patient, authenticated actor and conversation reference are required.")
    context = bind_patient(context, context.subject_id)
    await remove_conversation_reference(db, context, reference.strip())
    await db.commit()
    return {"deleted": True}
