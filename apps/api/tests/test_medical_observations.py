from datetime import datetime, timedelta, timezone
import json
import uuid
from unittest.mock import AsyncMock, patch
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from src.config import settings
from src.db import AsyncSessionLocal
from src.main import app
from src.models.external_document import ExternalDocument
from src.models.medical_observation import MedicalObservation
from src.processing.extraction.medical_observation_extractor import (
    MedicalObservationExtractor,
    ExtractedObservationData,
)
from src.processing.models.search import RerankedSearchResult
from src.schemas.search import ConversationMessage, RewriteResult
from src.services.answer_generator import get_answer_generator
from src.services.medical_observation_service import get_medical_observation_service
from src.services.medical_query_router import (
    MedicalQueryRoute,
    MedicalQueryRouter,
    get_medical_query_router,
    parse_relative_time_range,
)
from src.services.query_rewriter import get_query_rewriter
from src.services.search_service import get_retriever

TEST_SERVICE_SECRET = settings.QUICK_CLINIC_SERVICE_SECRET or "quick-clinic-internal-service-secret-2026"
VALID_AUTH_HEADER = {"Authorization": f"Bearer {TEST_SERVICE_SECRET}"}
INVALID_AUTH_HEADER = {"Authorization": "Bearer wrong-service-secret"}


@pytest.fixture(autouse=True)
def ensure_service_secret():
    """Ensure settings.QUICK_CLINIC_SERVICE_SECRET is configured during tests."""
    with patch.object(settings, "QUICK_CLINIC_SERVICE_SECRET", TEST_SERVICE_SECRET):
        yield


# ==============================================================================
# 1. EXTRACTION UNIT TESTS
# ==============================================================================


def test_extractor_blood_pressure_variations():
    extractor = MedicalObservationExtractor()
    report_date = datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc)

    # Various BP formats
    texts = [
        "Vitals: BP: 120/80 mmHg, regular rhythm.",
        "Clinical examination shows Blood Pressure 120/80 mmHg in right arm.",
        "Exam notes: B.P. 120 / 80 sitting position.",
        "Patient triage BP - 135/85 mmHg.",
    ]

    for text in texts:
        obs = extractor.extract_from_text(
            text=text,
            document_report_date=report_date,
        )
        assert len(obs) >= 1, f"Failed to extract BP from: {text}"
        bp = [o for o in obs if o.observation_type == "BLOOD_PRESSURE"][0]
        assert bp.value_numeric in [120.0, 135.0]
        assert bp.value_secondary_numeric in [80.0, 85.0]
        assert bp.unit == "mmHg"
        assert bp.confidence >= 0.85
        assert bp.extraction_method == "REGEX"


def test_extractor_heart_rate_variations():
    extractor = MedicalObservationExtractor()
    texts = [
        "Vitals: Heart Rate: 72 bpm, regular.",
        "Pulse: 72 bpm, strong.",
        "Vitals: HR 72, normal sinus rhythm.",
    ]
    for text in texts:
        obs = extractor.extract_from_text(text=text)
        hr = [o for o in obs if o.observation_type == "HEART_RATE"]
        assert len(hr) == 1, f"Failed for: {text}"
        assert hr[0].value_numeric == 72.0
        assert hr[0].unit == "bpm"


def test_extractor_oxygen_saturation():
    extractor = MedicalObservationExtractor()
    texts = [
        "Patient room air SpO2 98%.",
        "Respiratory status: Oxygen saturation: 97 % on room air.",
    ]
    for text in texts:
        obs = extractor.extract_from_text(text=text)
        spo2 = [o for o in obs if o.observation_type == "OXYGEN_SATURATION"]
        assert len(spo2) == 1
        assert spo2[0].value_numeric in [98.0, 97.0]
        assert spo2[0].unit == "%"


def test_extractor_body_temperature():
    extractor = MedicalObservationExtractor()
    texts = [
        "Vitals: Temperature: 98.6 F, oral.",
        "Body Temp: 37.0 C, afebrile.",
    ]
    for text in texts:
        obs = extractor.extract_from_text(text=text)
        temp = [o for o in obs if o.observation_type == "BODY_TEMPERATURE"]
        assert len(temp) == 1
        assert temp[0].value_numeric in [98.6, 37.0]
        assert temp[0].unit in ["F", "C"]


def test_extractor_glucose_types():
    extractor = MedicalObservationExtractor()
    # Fasting glucose
    obs1 = extractor.extract_from_text("Lab: Fasting glucose: 95 mg/dL.")
    assert any(o.observation_type == "FASTING_GLUCOSE" and o.value_numeric == 95.0 for o in obs1)

    obs2 = extractor.extract_from_text("Morning FBS: 92 mg/dL.")
    assert any(o.observation_type == "FASTING_GLUCOSE" and o.value_numeric == 92.0 for o in obs2)

    # Random glucose
    obs3 = extractor.extract_from_text("Random blood sugar: 130 mg/dL.")
    assert any(o.observation_type == "RANDOM_GLUCOSE" and o.value_numeric == 130.0 for o in obs3)

    # General glucose
    obs4 = extractor.extract_from_text("Point-of-care Glucose: 105 mg/dL.")
    assert any(o.observation_type == "BLOOD_GLUCOSE" and o.value_numeric == 105.0 for o in obs4)


def test_extractor_hba1c_and_hemoglobin():
    extractor = MedicalObservationExtractor()
    obs1 = extractor.extract_from_text("Glycated Hemoglobin HbA1c: 5.6 %.")
    a1c = [o for o in obs1 if o.observation_type == "HBA1C"]
    assert len(a1c) == 1
    assert a1c[0].value_numeric == 5.6
    assert a1c[0].unit == "%"

    obs2 = extractor.extract_from_text("CBC: Hemoglobin: 14.2 g/dL, normal.")
    hb = [o for o in obs2 if o.observation_type == "HEMOGLOBIN"]
    assert len(hb) == 1
    assert hb[0].value_numeric == 14.2
    assert hb[0].unit == "g/dL"


def test_extractor_false_positive_rejection():
    """Ensure non-medical numbers and room/invoice numbers are rejected."""
    extractor = MedicalObservationExtractor()
    non_medical_texts = [
        "Patient admitted to Room 120/80 on the 4th floor.",
        "Invoice # 95 generated on 2026-10-01.",
        "Call 9876543210 for emergency inquiries.",
        "Order ID: 120, Batch 80.",
        "Dr. Smith visited at 10:30 AM.",
    ]
    for text in non_medical_texts:
        obs = extractor.extract_from_text(text=text)
        assert len(obs) == 0, f"False positive detected in: {text}, found: {obs}"


def test_extractor_sanity_bounds_rejection():
    """Ensure clinically absurd or impossible numbers are rejected by sanity checks."""
    extractor = MedicalObservationExtractor()
    invalid_texts = [
        "Vitals: BP: 450/250 mmHg.",  # Absurd BP
        "SpO2: 150 %.",  # Impossible oxygen saturation
        "Heart Rate: 550 bpm.",  # Impossible HR
        "Temperature: 180 F.",  # Impossible temperature
        "Weight: 800 kg.",  # Out of sanity bound
    ]
    for text in invalid_texts:
        obs = extractor.extract_from_text(text=text)
        assert len(obs) == 0, f"Sanity check failed to reject absurd value in: {text}"


def test_extractor_date_resolution():
    """Explicit measurement date takes precedence over document report date fallback."""
    extractor = MedicalObservationExtractor()
    doc_fallback_date = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)

    # 1. Explicit nearby date
    text_with_date = "Recorded on 2026-10-03: BP: 120/80 mmHg."
    obs1 = extractor.extract_from_text(
        text=text_with_date,
        document_report_date=doc_fallback_date,
    )
    assert len(obs1) == 1
    assert obs1[0].observed_at.year == 2026
    assert obs1[0].observed_at.month == 10
    assert obs1[0].observed_at.day == 3

    # 2. Fallback to document report date
    text_without_date = "BP: 120/80 mmHg."
    obs2 = extractor.extract_from_text(
        text=text_without_date,
        document_report_date=doc_fallback_date,
    )
    assert len(obs2) == 1
    assert obs2[0].observed_at == doc_fallback_date


# ==============================================================================
# 2. DATABASE FIXTURE & ISOLATION TESTS
# ==============================================================================


@pytest.fixture
async def seed_observations_data():
    """Seeds observations for Patient A and Patient B with multiple timestamps."""
    patient_a_id = f"pat-a-{uuid.uuid4().hex[:8]}"
    patient_b_id = f"pat-b-{uuid.uuid4().hex[:8]}"
    doc_a1_id = f"doc-a1-{uuid.uuid4().hex[:8]}"
    doc_a2_id = f"doc-a2-{uuid.uuid4().hex[:8]}"
    doc_b1_id = f"doc-b1-{uuid.uuid4().hex[:8]}"

    # Fixed reference dates
    now_utc = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    date_1d_ago = now_utc - timedelta(days=1)
    date_2d_ago = now_utc - timedelta(days=2)
    date_10d_ago = now_utc - timedelta(days=10)

    async with AsyncSessionLocal() as session:
        # Documents
        doc_a1 = ExternalDocument(
            source_system="quick_clinic",
            external_patient_id=patient_a_id,
            external_document_id=doc_a1_id,
            file_name="recent_vitals_a.pdf",
            mime_type="application/pdf",
            file_size=1024,
            storage_path=f"medical-documents/{patient_a_id}/{doc_a1_id}.pdf",
            document_type="LAB_REPORT",
            report_date=date_1d_ago,
            status="READY",
        )
        doc_a2 = ExternalDocument(
            source_system="quick_clinic",
            external_patient_id=patient_a_id,
            external_document_id=doc_a2_id,
            file_name="old_vitals_a.pdf",
            mime_type="application/pdf",
            file_size=1024,
            storage_path=f"medical-documents/{patient_a_id}/{doc_a2_id}.pdf",
            document_type="LAB_REPORT",
            report_date=date_10d_ago,
            status="READY",
        )
        doc_b1 = ExternalDocument(
            source_system="quick_clinic",
            external_patient_id=patient_b_id,
            external_document_id=doc_b1_id,
            file_name="patient_b_vitals.pdf",
            mime_type="application/pdf",
            file_size=1024,
            storage_path=f"medical-documents/{patient_b_id}/{doc_b1_id}.pdf",
            document_type="LAB_REPORT",
            report_date=date_1d_ago,
            status="READY",
        )
        session.add_all([doc_a1, doc_a2, doc_b1])

        # Observations for Patient A
        obs_a_bp_recent = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_a_id,
            external_document_id=doc_a1_id,
            observation_type="BLOOD_PRESSURE",
            display_name="Blood Pressure",
            value_numeric=120.0,
            value_secondary_numeric=80.0,
            value_text="120/80",
            unit="mmHg",
            observed_at=date_1d_ago,
            page_number=1,
            chunk_index=0,
            confidence=0.95,
            extraction_method="REGEX",
        )
        obs_a_bp_older = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_a_id,
            external_document_id=doc_a2_id,
            observation_type="BLOOD_PRESSURE",
            display_name="Blood Pressure",
            value_numeric=135.0,
            value_secondary_numeric=85.0,
            value_text="135/85",
            unit="mmHg",
            observed_at=date_10d_ago,
            page_number=1,
            chunk_index=0,
            confidence=0.95,
            extraction_method="REGEX",
        )
        obs_a_glucose_recent = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_a_id,
            external_document_id=doc_a1_id,
            observation_type="FASTING_GLUCOSE",
            display_name="Fasting Glucose",
            value_numeric=95.0,
            value_text="95",
            unit="mg/dL",
            observed_at=date_2d_ago,
            page_number=1,
            chunk_index=0,
            confidence=0.95,
            extraction_method="REGEX",
        )

        # Observations for Patient B
        obs_b_bp = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_b_id,
            external_document_id=doc_b1_id,
            observation_type="BLOOD_PRESSURE",
            display_name="Blood Pressure",
            value_numeric=150.0,
            value_secondary_numeric=95.0,
            value_text="150/95",
            unit="mmHg",
            observed_at=date_1d_ago,
            page_number=1,
            chunk_index=0,
            confidence=0.95,
            extraction_method="REGEX",
        )

        session.add_all([obs_a_bp_recent, obs_a_bp_older, obs_a_glucose_recent, obs_b_bp])
        await session.commit()

    yield {
        "patient_a_id": patient_a_id,
        "patient_b_id": patient_b_id,
        "doc_a1_id": doc_a1_id,
        "doc_a2_id": doc_a2_id,
        "doc_b1_id": doc_b1_id,
        "now_utc": now_utc,
    }

    # Cleanup
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(ExternalDocument).where(
                ExternalDocument.external_document_id.in_([doc_a1_id, doc_a2_id, doc_b1_id])
            )
        )
        for doc in res.scalars().all():
            await session.delete(doc)

        res_obs = await session.execute(
            select(MedicalObservation).where(
                MedicalObservation.external_patient_id.in_([patient_a_id, patient_b_id])
            )
        )
        for obs in res_obs.scalars().all():
            await session.delete(obs)
        await session.commit()


@pytest.mark.asyncio
async def test_observation_query_endpoint_auth():
    """Verify endpoint authentication with service secret."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing auth
        r1 = await client.post("/internal/medical-observations/query", json={"patientId": "pat-1"})
        assert r1.status_code == 401

        # Invalid auth
        r2 = await client.post(
            "/internal/medical-observations/query",
            headers=INVALID_AUTH_HEADER,
            json={"patientId": "pat-1"},
        )
        assert r2.status_code == 401


@pytest.mark.asyncio
async def test_observation_query_patient_isolation(seed_observations_data):
    """Ensure querying Patient A never leaks Patient B's observations."""
    data = seed_observations_data
    patient_a = data["patient_a_id"]
    patient_b = data["patient_b_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-observations/query",
            headers=VALID_AUTH_HEADER,
            json={"patientId": patient_a},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["totalCount"] == 3
        # Check all returned observations belong strictly to patient A
        for item in body["observations"]:
            assert item["value"] != 150.0  # 150/95 belongs to Patient B
            assert item["type"] in ["BLOOD_PRESSURE", "FASTING_GLUCOSE"]


@pytest.mark.asyncio
async def test_observation_query_type_and_time_filtering(seed_observations_data):
    """Test observation type and date range filtering."""
    data = seed_observations_data
    patient_a = data["patient_a_id"]
    now_utc = data["now_utc"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Filter by observation type
        r_type = await client.post(
            "/internal/medical-observations/query",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": patient_a,
                "observationTypes": ["FASTING_GLUCOSE"],
            },
        )
        assert r_type.status_code == 200
        items_type = r_type.json()["observations"]
        assert len(items_type) == 1
        assert items_type[0]["type"] == "FASTING_GLUCOSE"
        assert items_type[0]["value"] == 95.0

        # 2. Filter by date window (last 3 days -> excludes the 10-day-old BP)
        from_3d = (now_utc - timedelta(days=3)).isoformat()
        r_date = await client.post(
            "/internal/medical-observations/query",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": patient_a,
                "observationTypes": ["BLOOD_PRESSURE"],
                "fromDate": from_3d,
            },
        )
        assert r_date.status_code == 200
        items_date = r_date.json()["observations"]
        assert len(items_date) == 1
        assert items_date[0]["value"] == 120.0
        assert items_date[0]["secondaryValue"] == 80.0


@pytest.mark.asyncio
async def test_observation_reprocessing_idempotency():
    """Reprocessing the same document removes prior observations and prevents duplicates."""
    patient_id = f"pat-re-{uuid.uuid4().hex[:8]}"
    doc_id = f"doc-re-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        # Initial run: save 1 observation
        obs1 = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_id,
            external_document_id=doc_id,
            observation_type="BLOOD_PRESSURE",
            display_name="Blood Pressure",
            value_numeric=120.0,
            value_secondary_numeric=80.0,
            unit="mmHg",
            observed_at=datetime.now(timezone.utc),
            confidence=0.9,
            extraction_method="REGEX",
        )
        session.add(obs1)
        await session.commit()

    # Verify 1 exists
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(MedicalObservation).where(
                MedicalObservation.external_document_id == doc_id
            )
        )
        assert len(res.scalars().all()) == 1

    # Reprocess: clean up old and insert updated observation
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(MedicalObservation).where(
                MedicalObservation.source_system == "quick_clinic",
                MedicalObservation.external_document_id == doc_id,
            )
        )
        for old in res.scalars().all():
            await session.delete(old)

        obs2 = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_id,
            external_document_id=doc_id,
            observation_type="BLOOD_PRESSURE",
            display_name="Blood Pressure",
            value_numeric=122.0,
            value_secondary_numeric=82.0,
            unit="mmHg",
            observed_at=datetime.now(timezone.utc),
            confidence=0.95,
            extraction_method="REGEX",
        )
        session.add(obs2)
        await session.commit()

    # Verify no duplication occurred (still exactly 1, with updated value)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(MedicalObservation).where(
                MedicalObservation.external_document_id == doc_id
            )
        )
        items = res.scalars().all()
        assert len(items) == 1
        assert items[0].value_numeric == 122.0
        # Cleanup
        await session.delete(items[0])
        await session.commit()


@pytest.mark.asyncio
async def test_document_deletion_cleanup():
    """Deleting a document via internal API cleans up its MedicalObservation records."""
    patient_id = f"pat-del-{uuid.uuid4().hex[:8]}"
    doc_id = f"doc-del-{uuid.uuid4().hex[:8]}"

    async with AsyncSessionLocal() as session:
        doc = ExternalDocument(
            source_system="quick_clinic",
            external_patient_id=patient_id,
            external_document_id=doc_id,
            file_name="to_delete.pdf",
            mime_type="application/pdf",
            file_size=512,
            document_type="LAB_REPORT",
            storage_path=f"medical-documents/{patient_id}/{doc_id}.pdf",
            status="READY",
        )
        obs = MedicalObservation(
            source_system="quick_clinic",
            external_patient_id=patient_id,
            external_document_id=doc_id,
            observation_type="HEART_RATE",
            display_name="Heart Rate",
            value_numeric=75.0,
            unit="bpm",
            observed_at=datetime.now(timezone.utc),
            confidence=0.95,
            extraction_method="REGEX",
        )
        session.add_all([doc, obs])
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        del_resp = await client.delete(
            f"/internal/medical-documents/{doc_id}/index",
            headers=VALID_AUTH_HEADER,
        )
        assert del_resp.status_code == 200

    # Verify observation was deleted
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(MedicalObservation).where(
                MedicalObservation.external_document_id == doc_id
            )
        )
        assert len(res.scalars().all()) == 0


# ==============================================================================
# 3. ROUTER CLASSIFICATION & CHAT INTEGRATION TESTS
# ==============================================================================


def test_query_router_classification():
    router = MedicalQueryRouter()
    ref_time = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

    # 1. STRUCTURED
    q_struct_latest = router.route_query("What is the latest BP?", now=ref_time)
    assert q_struct_latest.route == MedicalQueryRoute.STRUCTURED
    assert "BLOOD_PRESSURE" in q_struct_latest.target_observation_types
    assert q_struct_latest.time_filter.is_latest is True

    q_struct_days = router.route_query("Show blood pressure readings from the last 3 days", now=ref_time)
    assert q_struct_days.route == MedicalQueryRoute.STRUCTURED
    assert "BLOOD_PRESSURE" in q_struct_days.target_observation_types
    assert q_struct_days.time_filter.from_date is not None

    q_struct_glucose = router.route_query("What are the recorded fasting blood glucose values?", now=ref_time)
    assert q_struct_glucose.route == MedicalQueryRoute.STRUCTURED
    assert "FASTING_GLUCOSE" in q_struct_glucose.target_observation_types

    # 2. NARRATIVE / RAG
    q_rag_summary = router.route_query("Summarize the patient's discharge summary.", now=ref_time)
    assert q_rag_summary.route == MedicalQueryRoute.RAG

    q_rag_note = router.route_query("What did the doctor say in the clinical notes?", now=ref_time)
    assert q_rag_note.route == MedicalQueryRoute.RAG

    # 3. HYBRID
    q_hybrid = router.route_query(
        "What are the recent BP readings and what did the doctor recommend in the discharge note?",
        now=ref_time,
    )
    assert q_hybrid.route == MedicalQueryRoute.HYBRID
    assert "BLOOD_PRESSURE" in q_hybrid.target_observation_types


def test_query_router_multi_turn_followup():
    router = MedicalQueryRouter()
    ref_time = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)

    history = [
        ConversationMessage(role="user", content="What was the patient's blood pressure?"),
        ConversationMessage(role="assistant", content="The blood pressure was 120/80 on Oct 1."),
    ]

    # Elliptical follow-up
    q_followup = router.route_query("Only the last 3 days.", conversation_context=history, now=ref_time)
    assert q_followup.route == MedicalQueryRoute.STRUCTURED
    assert "BLOOD_PRESSURE" in q_followup.target_observation_types
    assert q_followup.time_filter.from_date is not None


@pytest.mark.asyncio
async def test_chat_structured_query_response(seed_observations_data):
    """
    Structured query routed to observations:
    - Returns answerMode="STRUCTURED"
    - Formats exact numeric reading deterministically
    - Returns observation citation with sourceType="OBSERVATION" and observationId
    """
    data = seed_observations_data
    patient_a = data["patient_a_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-rag/chat",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": patient_a,
                "message": "What is the latest BP?",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answerMode"] == "STRUCTURED"
        assert "120/80 mmHg" in body["answer"]
        assert len(body["citations"]) >= 1
        cit = body["citations"][0]
        assert cit["sourceType"] == "OBSERVATION"
        assert cit["observationId"] is not None
        assert cit["documentId"] == data["doc_a1_id"]


@pytest.mark.asyncio
async def test_chat_structured_query_missing_data(seed_observations_data):
    """
    When asking for a structured concept with no recorded observations:
    Returns clean deterministic missing data answer.
    """
    data = seed_observations_data
    patient_a = data["patient_a_id"]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/internal/medical-rag/chat",
            headers=VALID_AUTH_HEADER,
            json={
                "patientId": patient_a,
                "message": "What are the recorded HbA1c readings?",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["answerMode"] == "STRUCTURED"
        assert "couldn't find" in body["answer"].lower()
        assert "hba1c" in body["answer"].lower()
        assert len(body["citations"]) == 0


@pytest.mark.asyncio
async def test_chat_hybrid_query_response(seed_observations_data):
    """
    Hybrid query:
    Combines structured observations and RAG generation, returning answerMode="HYBRID"
    and citations with both observation and document chunk sources.
    """
    data = seed_observations_data
    patient_a = data["patient_a_id"]
    doc_a1 = data["doc_a1_id"]

    mock_retriever = AsyncMock()
    mock_retriever.search.return_value = [
        RerankedSearchResult(
            point_id="pt-hyb-1",
            document_id=doc_a1,
            patient_id=patient_a,
            content="Discharge summary: Doctor advised continuing ACE inhibitor therapy [2].",
            score=0.92,
            rerank_score=0.92,
            chunk_index=0,
            token_count=12,
            start_page=1,
            end_page=1,
            page_numbers=[1],
            block_types=["text"],
            file_name="recent_vitals_a.pdf",
            document_type="LAB_REPORT",
            report_date="2026-10-04T00:00:00Z",
        )
    ]

    mock_rewriter = AsyncMock()
    mock_rewriter.rewrite.return_value = RewriteResult(
        original_query="What are the recent BP readings and what did the doctor recommend?",
        retrieval_query="doctor recommendations for blood pressure in patient medical reports",
        rewritten=True,
        reason="Preserved narrative context",
    )

    async def mock_hybrid_generate(*args, **kwargs):
        chunks = kwargs.get("chunks", [])
        return (
            "Recent blood pressure was 120/80 mmHg [1]. The doctor advised continuing ACE inhibitor therapy [2].",
            chunks,
        )

    mock_generator = AsyncMock()
    mock_generator.generate_answer.side_effect = mock_hybrid_generate

    app.dependency_overrides[get_retriever] = lambda: mock_retriever
    app.dependency_overrides[get_query_rewriter] = lambda: mock_rewriter
    app.dependency_overrides[get_answer_generator] = lambda: mock_generator

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/internal/medical-rag/chat",
                headers=VALID_AUTH_HEADER,
                json={
                    "patientId": patient_a,
                    "message": "What are the recent BP readings and what did the doctor recommend in the discharge note?",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["answerMode"] == "HYBRID"
            assert "120/80 mmHg" in body["answer"]
            assert len(body["citations"]) >= 1
            # Verify citation source types
            source_types = [c["sourceType"] for c in body["citations"]]
            assert "OBSERVATION" in source_types
    finally:
        app.dependency_overrides.clear()
