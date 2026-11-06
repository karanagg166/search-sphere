"""Provision only the signed, dedicated synthetic release account and its policy."""

import hashlib
import hmac
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from src.models.inference_data_policy import InferenceDataPolicy
from src.models.user import User
from src.security.jwt import hash_password

RELEASE_EMAIL = "search-sphere-release@example.com"
RELEASE_USER_ID = str(uuid.uuid5(uuid.NAMESPACE_URL, "search-sphere:synthetic-release:" + RELEASE_EMAIL))


async def provision_release_identity(db, email, password, fixture_key):
    if email != RELEASE_EMAIL or len(password.encode()) < 32 or len(fixture_key.encode()) < 32:
        raise ValueError("A dedicated synthetic identity and strong protected credentials are required.")
    marker = hmac.new(fixture_key.encode(), b"search-sphere-release-identity-v1", hashlib.sha256).hexdigest()
    name, approval = "Synthetic release " + marker[:24], "signed-release-fixtures:" + marker
    # Never fetch existing password hashes or unrelated user/record contents.
    current = (await db.execute(select(User.id, User.name, User.auth_provider, User.is_active).where(
        (User.email == email) | (User.id == RELEASE_USER_ID)).with_for_update())).all()
    if current and (len(current) != 1 or current[0].id != RELEASE_USER_ID or current[0].name != name
        or current[0].auth_provider != "release_synthetic" or not current[0].is_active):
        raise ValueError("The release identity conflicts with an existing or revoked account.")
    password_hash = hash_password(password)
    if current:
        await db.execute(update(User).where(User.id == RELEASE_USER_ID).values(hashed_password=password_hash))
    else:
        db.add(User(id=RELEASE_USER_ID, email=email, name=name, auth_provider="release_synthetic", hashed_password=password_hash))
        await db.flush()
    identity = ("search_sphere", RELEASE_USER_ID, "", RELEASE_USER_ID)
    policy = await db.get(InferenceDataPolicy, identity, with_for_update=True)
    if policy and (policy.approval_ref != approval or policy.classification != "synthetic" or not policy.enabled):
        raise ValueError("The release provider policy was changed or revoked; operator review is required.")
    if policy is None:
        policy = InferenceDataPolicy(client_id=identity[0], tenant_id=identity[1], collection_scope=identity[2],
            subject_scope=identity[3], classification="synthetic", approval_ref=approval, enabled=True)
        db.add(policy)
    policy.expires_at = datetime.now(UTC) + timedelta(days=30)
    return RELEASE_USER_ID
