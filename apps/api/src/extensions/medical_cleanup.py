from sqlalchemy import delete

from src.models.medical_observation import MedicalObservation


async def delete_observations(session, document):
    """Clean extension rows in the same transaction as generic metadata deletion."""
    await session.execute(delete(MedicalObservation).where(
        MedicalObservation.source_system == document.client_id,
        MedicalObservation.tenant_id == document.tenant_id,
        MedicalObservation.external_patient_id == document.owner_subject_id,
        MedicalObservation.external_document_id == document.external_document_id,
    ))
