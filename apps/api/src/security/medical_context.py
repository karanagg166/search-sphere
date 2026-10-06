"""Compatibility adapter authorization; medical identity never enters the generic core."""
import hashlib

from fastapi import Depends, HTTPException, Request

from src.security.service_auth import _authenticate_service_context
from src.security.service_context import ServiceContext


def patient_collection_id(patient_id: str) -> str:
    return f"patient_{hashlib.sha256(patient_id.encode()).hexdigest()[:32]}_records"


async def get_medical_context(
    request: Request,
    context: ServiceContext = Depends(_authenticate_service_context),
) -> ServiceContext:
    if context.client_id != "quick_clinic":
        raise HTTPException(status_code=403, detail="Credential is not authorized for the medical compatibility adapter.")
    # Legacy storage keys do not include a tenant segment. Keep this adapter
    # limited to the initial platform tenant; future clinics use the generic API.
    if context.tenant_id != "quick_clinic_default":
        raise HTTPException(status_code=403, detail="Medical adapter tenant is not supported.")
    path = request.url.path
    if request.method == "DELETE":
        scope = "documents:delete"
    elif "medical-rag" in path:
        scope = "answers:generate"
    elif "medical-retrieval" in path:
        scope = "search:execute"
    elif request.method == "POST" and "medical-documents" in path:
        scope = "documents:write"
    else:
        scope = "documents:read"
    context.require_scope(scope)
    return context


def bind_patient(context: ServiceContext, patient_id: str) -> ServiceContext:
    return context.resolve_scope(patient_collection_id(patient_id), patient_id)


def validate_storage_scope(context: ServiceContext, path: str) -> None:
    if ".." in path:
        raise HTTPException(status_code=400, detail="Path traversal is not permitted.")
    parts = path.split("/")
    if len(parts) != 4 or parts[0] != "medical-documents" or any(p in ("", ".", "..") for p in parts):
        raise HTTPException(status_code=400, detail="Invalid medical storage path.")
    bind_patient(context, parts[1])
