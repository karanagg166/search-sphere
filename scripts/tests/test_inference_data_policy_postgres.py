from datetime import UTC, datetime, timedelta

import pytest
from ingestion_test_support import approve
from ingestion_test_support import fixture as fixture  # noqa: PLC0414 -- pytest fixture registration
from sqlalchemy import update
from src.inference.data_policy import classification_for_scope
from src.inference.policy import DataClassification
from src.models.inference_data_policy import InferenceDataPolicy


@pytest.mark.asyncio
async def test_specific_revocation_and_expiry_cannot_fall_through_to_broad_approval(fixture):
    await approve(fixture)  # Broad synthetic owner tenant approval.
    await approve(fixture, subject=fixture.owner_id)
    async with fixture.sessions() as db, db.begin():
        specific = (InferenceDataPolicy.tenant_id == fixture.owner_id, InferenceDataPolicy.subject_scope == fixture.owner_id)
        assert await classification_for_scope(db, "search_sphere", fixture.owner_id, subject_id=fixture.owner_id) == DataClassification.SYNTHETIC
        await db.execute(update(InferenceDataPolicy).where(*specific).values(enabled=False))
        assert await classification_for_scope(db, "search_sphere", fixture.owner_id, subject_id=fixture.owner_id) == DataClassification.CONFIDENTIAL
        await db.execute(update(InferenceDataPolicy).where(*specific).values(enabled=True, expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        assert await classification_for_scope(db, "search_sphere", fixture.owner_id, subject_id=fixture.owner_id) == DataClassification.CONFIDENTIAL
        assert await classification_for_scope(db, "quick_clinic", "unapproved-tenant", subject_id="another-patient") == DataClassification.MEDICAL
        assert await classification_for_scope(db, "another-client", fixture.owner_id) == DataClassification.CONFIDENTIAL


@pytest.mark.asyncio
async def test_equal_specificity_conflicts_choose_the_more_restrictive_classification(fixture):
    await approve(fixture, subject=fixture.owner_id)
    async with fixture.sessions() as db, db.begin():
        db.add(InferenceDataPolicy(
            client_id="search_sphere", tenant_id=fixture.owner_id, collection_scope="restricted-class", subject_scope="",
            classification="confidential", approval_ref="synthetic-policy-conflict-fixture", expires_at=datetime.now(UTC) + timedelta(hours=1),
        ))
        await db.flush()
        assert await classification_for_scope(db, "search_sphere", fixture.owner_id, "restricted-class", fixture.owner_id) == DataClassification.CONFIDENTIAL


