"""Signed historical reconciliation proof; contains only synthetic identity/metadata."""
import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime, timedelta

from release_gate import PROJECT

OWNER = str(uuid.uuid5(uuid.NAMESPACE_URL, "search-sphere:synthetic-release:search-sphere-release@example.com"))
COUNTS = {"documents", "conversations", "vectors", "active_jobs", "pending_objects",
    "structured_evidence", "published_versions", "private_objects"}
FIELDS = {"version", "owner", "project", "through_run_id", "source_sha", "api_sha",
    "created_at", "target_fingerprint", "counts"}


def _signature(body, key):
    if not isinstance(key, str) or len(key.encode()) < 32:
        raise ValueError("Private fixture key required")
    data = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hmac.new(key.encode(), b"native-reconciled-scope-v1\n" + data, hashlib.sha256).hexdigest()


def verify_baseline(value, key):
    if not isinstance(value, dict) or set(value) != FIELDS | {"signature"}:
        raise ValueError("Invalid reconciliation format")
    body = {field: value[field] for field in FIELDS}
    if (type(body["version"]) is not int or body["version"] != 1 or body["owner"] != OWNER
        or body["project"] != PROJECT or not isinstance(body["counts"], dict)
        or set(body["counts"]) != COUNTS
        or any(type(count) is not int or count != 0 for count in body["counts"].values())):
        raise ValueError("Unverified reconciliation scope")
    for field, pattern in (("through_run_id", r"[1-9][0-9]{0,19}"), ("source_sha", r"[a-f0-9]{40}"),
        ("api_sha", r"[a-f0-9]{40}"), ("target_fingerprint", r"[a-f0-9]{64}")):
        if not isinstance(body[field], str) or not re.fullmatch(pattern, body[field]):
            raise ValueError("Invalid reconciliation identity")
    if not isinstance(body["created_at"], str):
        raise TypeError("Invalid reconciliation time")
    created = datetime.fromisoformat(body["created_at"])
    if created.tzinfo is None or created > datetime.now(UTC) + timedelta(minutes=5):
        raise ValueError("Invalid reconciliation time")
    if (not isinstance(value["signature"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["signature"])
        or not hmac.compare_digest(value["signature"], _signature(body, key))):
        raise ValueError("Invalid reconciliation signature")
    return body


def seal_baseline(proof, run_id, source_sha, api_sha, target, key):
    body = {"version": 1, "owner": proof["owner"], "project": PROJECT,
        "through_run_id": run_id, "source_sha": source_sha, "api_sha": api_sha,
        "created_at": datetime.now(UTC).isoformat(), "target_fingerprint": target, "counts": proof["counts"]}
    value = {**body, "signature": _signature(body, key)}
    verify_baseline(value, key)
    return value
