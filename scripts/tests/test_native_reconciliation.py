"""Protected operator admission and signed proof validation; no remote calls."""
import copy
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reconcile_native_fixtures as operator
from smoke_recovery_baseline import COUNTS, OWNER, seal_baseline, verify_baseline

KEY = "synthetic-native-baseline-key-with-32-bytes"


def baseline():
    return seal_baseline({"owner": OWNER, "counts": dict.fromkeys(COUNTS, 0)}, "123456",
        "a" * 40, "b" * 40, "c" * 64, KEY)


def test_signed_empty_scope_is_bound_to_identity_run_revisions_target_and_all_zero_counts():
    value = baseline()
    proof = verify_baseline(value, KEY)
    assert proof["owner"] == OWNER and proof["through_run_id"] == "123456"
    assert proof["source_sha"] == "a" * 40 and proof["api_sha"] == "b" * 40
    assert set(proof["counts"].values()) == {0}


@pytest.mark.parametrize("field,value", [
    ("owner", "foreign-owner"), ("project", "foreign-project"), ("through_run_id", "-1"),
    ("source_sha", "a" * 39), ("api_sha", "B" * 40), ("target_fingerprint", "wrong"),
    ("signature", "0" * 64), ("signature", "non-ascii-\u2603"),
    ("created_at", (datetime.now(UTC) + timedelta(days=1)).isoformat()),
])
def test_changed_untrusted_proof_cannot_skip_historical_receipts(field, value):
    proof = baseline()
    proof[field] = value
    with pytest.raises((ValueError, TypeError)):
        verify_baseline(proof, KEY)


@pytest.mark.parametrize("counts", [{}, {**dict.fromkeys(COUNTS, 0), "vectors": 1},
    {**dict.fromkeys(COUNTS, 0), "documents": False}])
def test_partial_nonzero_or_boolean_counts_cannot_be_signed(counts):
    with pytest.raises(ValueError):
        seal_baseline({"owner": OWNER, "counts": counts}, "123456", "a" * 40, "b" * 40, "c" * 64, KEY)


def test_unknown_fields_and_wrong_or_short_keys_fail_closed():
    value = baseline()
    with pytest.raises(ValueError):
        verify_baseline({**value, "ignore_missing_receipts": True}, KEY)
    for key in (KEY + "changed", "short"):
        with pytest.raises(ValueError):
            verify_baseline(value, key)
    assert copy.deepcopy(value) == value


@pytest.fixture
def environment(tmp_path, monkeypatch):
    values = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "karanagg166/search-sphere",
        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_WORKFLOW_REF": "karanagg166/search-sphere/.github/workflows/native-fixture-reconciliation.yml@refs/heads/main",
        "GITHUB_SHA": "a" * 40, "EXPECTED_SHA": "b" * 40, "GITHUB_RUN_ID": "123456",
        "RUNNER_TEMP": str(tmp_path), "NATIVE_BASELINE_PATH": str(tmp_path / "native-reconciliation" / "baseline.json"),
        "DATABASE_URL": "postgresql+asyncpg://postgres:synthetic-only@db.jyzjgoniuwexvipbbpwa.supabase.co/postgres"}
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    checked = []
    monkeypatch.setattr(operator, "trusted_ci", lambda sha: checked.append(sha))
    monkeypatch.setattr(operator, "request", lambda base, path: {"git_commit": "b" * 40,
        "status": "ready" if path == "/ready" else "ok", "database": "connected"})
    return values, checked


def test_operator_requires_both_exact_main_cis_and_private_exclusive_output(environment):
    values, checked = environment
    _, output = operator.admission()
    assert checked == ["a" * 40, "b" * 40]
    assert output == Path(values["NATIVE_BASELINE_PATH"])
    assert output.parent.stat().st_mode & 0o077 == 0 and not output.exists()
    output.write_text("preserve prior proof")
    with pytest.raises(operator.ReleaseError, match="exclusive"):
        operator.admission()
    assert output.read_text() == "preserve prior proof"


@pytest.mark.parametrize("field,value", [("GITHUB_REF", "refs/heads/untrusted"),
    ("GITHUB_EVENT_NAME", "pull_request"), ("GITHUB_REPOSITORY", "foreign/repo"),
    ("GITHUB_WORKFLOW_REF", "untrusted-workflow"), ("DATABASE_URL", "postgresql://synthetic@localhost/test"),
    ("NATIVE_BASELINE_PATH", "/tmp/untrusted-output.json")])
def test_wrong_event_resource_or_destination_is_refused_before_audit(environment, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(operator.ReleaseError):
        operator.admission()


def test_source_ci_or_serving_revision_failure_prevents_reconciliation(environment, monkeypatch):
    def denied(_):
        raise operator.ReleaseError("synthetic CI denial")
    monkeypatch.setattr(operator, "trusted_ci", denied)
    with pytest.raises(operator.ReleaseError, match="CI denial"):
        operator.admission()
    monkeypatch.setattr(operator, "trusted_ci", lambda _: None)
    monkeypatch.setattr(operator, "request", lambda *_: {"git_commit": "c" * 40, "status": "ok"})
    with pytest.raises(operator.ReleaseError, match="revision/readiness"):
        operator.admission()
