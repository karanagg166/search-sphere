"""Only the dedicated signed synthetic principal can receive release approval."""

import pytest
from ingestion_test_support import fixture as fixture  # noqa: PLC0414
from sqlalchemy import delete, select, update
from src.models.inference_data_policy import InferenceDataPolicy
from src.models.user import User
from src.services.release_identity import (
    RELEASE_EMAIL,
    RELEASE_USER_ID,
    provision_release_identity,
)


@pytest.mark.asyncio
async def test_release_seed_is_idempotent_and_scoped_and_does_not_reactivate_revoked_accounts(fixture):
    key, password = "synthetic-fixture-signing-key-32-bytes-long", "synthetic-release-password-32-bytes-long"
    try:
        for _ in range(2):
            async with fixture.sessions() as db, db.begin():
                assert await provision_release_identity(db, RELEASE_EMAIL, password, key) == RELEASE_USER_ID
        async with fixture.sessions() as db:
            policies = (await db.execute(select(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == RELEASE_USER_ID))).scalars().all()
            assert len(policies) == 1 and policies[0].classification == "synthetic"
            assert policies[0].subject_scope == RELEASE_USER_ID and policies[0].collection_scope == ""
        async with fixture.sessions() as db, db.begin():
            await db.execute(update(User).where(User.id == RELEASE_USER_ID).values(is_active=False))
        async with fixture.sessions() as db, db.begin():
            with pytest.raises(ValueError, match="revoked"):
                await provision_release_identity(db, RELEASE_EMAIL, password, key)
    finally:
        async with fixture.sessions() as db, db.begin():
            await db.execute(delete(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == RELEASE_USER_ID))
            await db.execute(delete(User).where(User.id == RELEASE_USER_ID))


@pytest.mark.asyncio
async def test_release_seed_refuses_other_accounts_or_changed_signing_keys(fixture):
    key, password = "synthetic-fixture-signing-key-32-bytes-long", "synthetic-release-password-32-bytes-long"
    try:
        async with fixture.sessions() as db, db.begin():
            with pytest.raises(ValueError):
                await provision_release_identity(db, "ordinary-user@example.invalid", password, key)
        async with fixture.sessions() as db, db.begin():
            await provision_release_identity(db, RELEASE_EMAIL, password, key)
        async with fixture.sessions() as db, db.begin():
            with pytest.raises(ValueError, match="conflicts"):
                await provision_release_identity(db, RELEASE_EMAIL, password, key + "changed")
    finally:
        async with fixture.sessions() as db, db.begin():
            await db.execute(delete(InferenceDataPolicy).where(InferenceDataPolicy.tenant_id == RELEASE_USER_ID))
            await db.execute(delete(User).where(User.id == RELEASE_USER_ID))
