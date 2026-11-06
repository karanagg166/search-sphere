"""Resolve data eligibility from server-owned PostgreSQL policy, never client metadata."""

from sqlalchemy import func, select

from src.inference.policy import DataClassification
from src.models.inference_data_policy import InferenceDataPolicy


async def classification_for_scope(db, client_id, tenant_id, collection_id=None, subject_id=None):
    rows = (await db.execute(select(InferenceDataPolicy, InferenceDataPolicy.expires_at > func.now()).where(
        InferenceDataPolicy.client_id == client_id,
        InferenceDataPolicy.tenant_id == tenant_id,
        InferenceDataPolicy.collection_scope.in_((collection_id or "", "")),
        InferenceDataPolicy.subject_scope.in_((subject_id or "", "")),
    ))).all()
    fallback = DataClassification.MEDICAL if client_id == "quick_clinic" else DataClassification.CONFIDENTIAL
    if not rows:
        return fallback
    def specificity(row):
        return bool(row[0].collection_scope) + bool(row[0].subject_scope)
    most_specific = max(specificity(row) for row in rows)
    selected = [row for row in rows if specificity(row) == most_specific]
    if any(not current or not policy.enabled or not policy.approval_ref.strip() for policy, current in selected):
        return fallback  # Revocation/expiry cannot fall through to a broader grant.
    restrictions = {"public": 0, "synthetic": 1, "confidential": 2, "medical": 3}
    return DataClassification(max((policy.classification for policy, _ in selected), key=restrictions.__getitem__))
