"""Current document authorization and immutable snapshots for exact clinical evidence."""

import hashlib
import json

from sqlalchemy import false, select

from src.models.external_document import ExternalDocument
from src.models.medical_observation import MedicalObservation


def observation_query(context):
    query = select(MedicalObservation).join(ExternalDocument, (
        (ExternalDocument.source_system == MedicalObservation.source_system)
        & (ExternalDocument.tenant_id == MedicalObservation.tenant_id)
        & (ExternalDocument.external_document_id == MedicalObservation.external_document_id)
        & (ExternalDocument.external_patient_id == MedicalObservation.external_patient_id)
    ))
    if not context.is_service_client or not context.subject_id or not context.collection_id:
        return query.where(false())
    return query.where(
        MedicalObservation.source_system == context.client_id, MedicalObservation.tenant_id == context.tenant_id,
        MedicalObservation.external_patient_id == context.subject_id,
        ExternalDocument.owner_subject_id == context.subject_id, ExternalDocument.collection_id == context.collection_id,
        ExternalDocument.status == "READY", MedicalObservation.extraction_method == "REGEX",
    )


def observation_fingerprint(row):
    fields = ["id", "source_system", "tenant_id", "external_patient_id", "external_document_id", "observation_type",
              "display_name", "value_numeric", "value_secondary_numeric", "value_text", "unit", "observed_at",
              "reported_at", "page_number", "chunk_index", "confidence", "extraction_method", "is_date_inferred"]
    value = json.dumps({name: getattr(row, name) for name in fields}, sort_keys=True, default=str)
    return hashlib.sha256(value.encode()).hexdigest()
