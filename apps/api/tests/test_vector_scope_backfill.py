from types import SimpleNamespace

from src.maintenance.backfill_vector_scope import plan_payload


def doc(tenant="quick_clinic_default", patient="synthetic-a"):
    return SimpleNamespace(client_id="quick_clinic", source_system="quick_clinic", tenant_id=tenant, collection_id="synthetic-collection", owner_subject_id=patient, external_patient_id=patient, external_document_id="same-doc")


def test_legacy_vector_gets_generic_scope():
    payload = {"source_system": "quick_clinic", "patient_id": "synthetic-a", "document_id": "same-doc"}
    assert plan_payload(payload, [doc()]) == {"client_id": "quick_clinic", "tenant_id": "quick_clinic_default", "collection_id": "synthetic-collection", "owner_subject_id": "synthetic-a"}


def test_ambiguous_legacy_vector_is_never_migrated():
    payload = {"source_system": "quick_clinic", "patient_id": "synthetic-a", "document_id": "same-doc"}
    assert plan_payload(payload, [doc(), doc(tenant="other-clinic")]) is None


def test_foreign_patient_or_existing_scope_is_never_overwritten():
    payload = {"source_system": "quick_clinic", "patient_id": "synthetic-b", "document_id": "same-doc"}
    assert plan_payload(payload, [doc()]) is None
    payload["patient_id"] = "synthetic-a"
    payload["client_id"] = "exam_arena"
    assert plan_payload(payload, [doc()]) is None
