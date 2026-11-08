"""Fresh-runner recovery boundaries; synthetic metadata and no application requests."""
import copy
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import restore_native_fixtures as restore
import smoke_recovery_baseline as baseline
from release_gate import REPOSITORY
from smoke_fixtures import FixtureManager, signed_conversation_title, signed_filename

KEY = "public-synthetic-native-restore-key-with-32-bytes"
RUN = "a" * 32
DOC = "11111111-1111-4111-8111-111111111111"
THREAD = "22222222-2222-4222-8222-222222222222"
CUTOFF = datetime.now(UTC) - timedelta(minutes=10)


def run(identity=150, after=True, status="completed"):
    return {"id": identity, "head_sha": "a" * 40, "status": status, "conclusion": "success",
        "updated_at": (CUTOFF + timedelta(minutes=1 if after else -1)).isoformat(),
        "created_at": (CUTOFF - timedelta(minutes=2)).isoformat()}


def proof():
    body = {"version": 1, "owner": baseline.OWNER, "project": baseline.PROJECT,
        "through_run_id": "100", "source_sha": "a" * 40, "api_sha": "b" * 40,
        "target_fingerprint": "c" * 64, "created_at": CUTOFF.isoformat(),
        "counts": dict.fromkeys(baseline.COUNTS, 0)}
    return {**body, "signature": baseline._signature(body, KEY)}


def receipts(manager, *, doc=DOC, status="pending_cleanup", thread=False):
    entry = {"run_id": RUN, "user_id": baseline.OWNER, "documents": [], "conversations": []}
    if thread:
        entry["conversations"] = [{"conversation_id": THREAD,
            "title": signed_conversation_title(baseline.OWNER, RUN), "status": status}]
    else:
        entry["documents"] = [{"doc_id": doc, "filename": signed_filename(baseline.OWNER, RUN), "status": status}]
    body = {"manifest_version": 2, "runs": {RUN: entry}}
    return {**body, "signature": manager._signature(body)}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_FIXTURE_KEY", KEY)
    values = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_RUN_ID": "200",
        "GITHUB_WORKFLOW_REF": REPOSITORY + "/.github/workflows/release.yml@refs/heads/main",
        "GITHUB_SHA": "a" * 40, "EXPECTED_SHA": "b" * 40, "GH_TOKEN": "synthetic-read-token"}
    for name, value in values.items(): monkeypatch.setenv(name, value)
    checked = []
    monkeypatch.setattr(restore, "trusted_ci", lambda sha: checked.append(sha))
    manager = FixtureManager(tmp_path / "receipts.json", "f" * 32)
    manager.save_manifest(manager.load_manifest())
    return manager, checked


def test_admission_requires_both_exact_main_cis(environment):
    _, checked = environment
    assert restore.admission() == 200
    assert checked == ["a" * 40, "b" * 40]


@pytest.mark.parametrize("field,value", [("GITHUB_REPOSITORY", "foreign/repo"),
    ("GITHUB_REF", "refs/heads/feature"), ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_WORKFLOW_REF", "foreign.yml"), ("GITHUB_RUN_ID", "-1")])
def test_untrusted_admission_denied(environment, monkeypatch, field, value):
    monkeypatch.setenv(field, value)
    with pytest.raises(restore.ReleaseError): restore.admission()


def test_baseline_binds_run_source_and_time_and_rejects_old_reruns(environment, monkeypatch):
    _, checked = environment
    operator = run(100)
    monkeypatch.setattr(restore, "workflow_runs", lambda workflow: [operator])
    monkeypatch.setattr(restore, "artifact_for", lambda run, name: {"id": 100})
    monkeypatch.setattr(restore, "archive_bytes", lambda artifact: b"synthetic")
    monkeypatch.setattr(restore, "archive_json", lambda payload, filename: proof())
    assert restore.reconciliation(200) == (100, CUTOFF)
    assert checked == ["a" * 40, "b" * 40]
    for change in [{"id": 200}, {"head_sha": "c" * 40}, {"created_at": (CUTOFF + timedelta(seconds=10)).isoformat()}]:
        monkeypatch.setattr(restore, "workflow_runs", lambda workflow, change=change: [{**operator, **change}])
        with pytest.raises(restore.ReleaseError): restore.reconciliation(200)


@pytest.mark.parametrize("change", ["signature", "owner", "document_id", "filename", "status", "version", "extra"])
def test_tampered_receipts_fail_before_recovery(environment, change):
    manager, _ = environment
    value = receipts(manager)
    row = value["runs"][RUN]["documents"][0]
    if change == "signature": value["signature"] = "0" * 64
    if change == "owner": value["runs"][RUN]["user_id"] = str(uuid.uuid4())
    if change == "document_id": row["doc_id"] = str(uuid.uuid4())
    if change == "filename": row["filename"] = "ordinary-private-file.pdf"
    if change == "status": row["status"] = "ignore"
    if change == "version": value["manifest_version"] = 1
    if change == "extra": value["skip_validation"] = True
    with pytest.raises((restore.ReleaseError, ValueError)): restore.pending_receipts(value, manager)
    assert manager.get_uncleaned_fixtures() == []


@pytest.mark.parametrize("change", ["owner", "filename", "status"])
def test_resigned_but_foreign_or_malformed_resources_denied(environment, change):
    manager, _ = environment
    value = receipts(manager)
    if change == "owner": value["runs"][RUN]["user_id"] = "foreign"
    if change == "filename": value["runs"][RUN]["documents"][0]["filename"] = "ordinary-private-file.pdf"
    if change == "status": value["runs"][RUN]["documents"][0]["status"] = "ignore"
    value["signature"] = manager._signature({key: item for key, item in value.items() if key != "signature"})
    with pytest.raises(restore.ReleaseError): restore.pending_receipts(value, manager)


def history(monkeypatch, runs, values):
    fetched = []
    monkeypatch.setattr(restore, "workflow_runs", lambda workflow: runs if workflow == "release.yml" else [])
    monkeypatch.setattr(restore, "artifact_for", lambda run, name: {"id": run["id"]} if run["id"] in values else None)
    monkeypatch.setattr(restore, "archive_bytes", lambda artifact: fetched.append(artifact["id"]) or artifact["id"])
    monkeypatch.setattr(restore, "archive_json", lambda identity, filename: copy.deepcopy(values[identity]))
    return fetched


def test_old_snapshot_runs_covered_but_later_reruns_replayed(environment, monkeypatch):
    manager, _ = environment
    fetched = history(monkeypatch, [run(50, after=False), run(60), run(201, status="in_progress")],
        {50: {"manifest_version": 1}, 60: receipts(manager)})
    restore.restore(manager, 200, 100, CUTOFF)
    assert fetched == [60]
    assert [row["doc_id"] for row in manager.get_uncleaned_fixtures()] == [DOC]


def test_old_active_run_is_not_covered_by_snapshot(environment, monkeypatch):
    manager, _ = environment
    history(monkeypatch, [run(50, after=False, status="in_progress")], {})
    with pytest.raises(restore.ReleaseError, match="still active"): restore.restore(manager, 200, 100, CUTOFF)


def test_unsigned_legacy_receipt_from_post_snapshot_rerun_requires_audit(environment, monkeypatch):
    manager, _ = environment
    history(monkeypatch, [run(50)], {50: {"manifest_version": 1}})
    with pytest.raises(restore.ReleaseError): restore.restore(manager, 200, 100, CUTOFF)


@pytest.mark.parametrize("skipped", [False, True])
def test_missing_receipts_require_confirmed_skipped_mutation(environment, monkeypatch, skipped):
    manager, _ = environment
    history(monkeypatch, [run()], {})
    monkeypatch.setattr(restore, "mutation_was_skipped", lambda run, workflow: skipped)
    if skipped: restore.restore(manager, 200, 100, CUTOFF)
    else:
        with pytest.raises(restore.ReleaseError, match="lacks signed receipts"): restore.restore(manager, 200, 100, CUTOFF)


@pytest.mark.parametrize("conclusion", ["skipped", "success", "failure", "cancelled", None])
def test_skip_proof_requires_exact_completed_mutation_step(environment, monkeypatch, conclusion):
    step = {"name": restore.WORKFLOWS["release.yml"][1], "status": "completed", "conclusion": conclusion}
    monkeypatch.setattr(restore, "github", lambda path: {"total_count": 1, "jobs": [{"steps": [step]}]})
    assert restore.mutation_was_skipped(run(), "release.yml") == (conclusion == "skipped")


def test_pending_wins_and_both_resource_types_persist_privately(environment, monkeypatch):
    manager, _ = environment
    history(monkeypatch, [run(150), run(160), run(170)], {150: receipts(manager),
        160: receipts(manager, status="cleaned_up"), 170: receipts(manager, thread=True)})
    restore.restore(manager, 200, 100, CUTOFF)
    assert len(manager.get_uncleaned_fixtures()) == len(manager.get_uncleaned_conversations()) == 1
    assert manager.get_uncleaned_fixtures()[0]["user_id"] == baseline.OWNER
    assert manager.get_uncleaned_conversations()[0]["conversation_id"] == THREAD
    assert manager.manifest_path.stat().st_mode & 0o077 == 0
    assert "signature" in json.loads(manager.manifest_path.read_text())


def test_capacity_failure_preserves_previous_journal(environment, monkeypatch):
    manager, _ = environment
    monkeypatch.setattr(manager, "MAX_ENTRIES", 1)
    before = manager.manifest_path.read_bytes()
    history(monkeypatch, [run(150), run(160)], {150: receipts(manager), 160: receipts(manager, doc=THREAD)})
    with pytest.raises(restore.ReleaseError, match="capacity"): restore.restore(manager, 200, 100, CUTOFF)
    assert manager.manifest_path.read_bytes() == before


def test_missing_baseline_or_invalid_signature_never_grants_a_historical_cutoff(environment, monkeypatch):
    monkeypatch.setattr(restore, "workflow_runs", lambda workflow: [])
    with pytest.raises(restore.ReleaseError): restore.reconciliation(200)
    monkeypatch.setattr(restore, "workflow_runs", lambda workflow: [run(100)])
    monkeypatch.setattr(restore, "artifact_for", lambda run, name: None)
    with pytest.raises(restore.ReleaseError): restore.reconciliation(200)
    monkeypatch.setattr(restore, "artifact_for", lambda run, name: {"id": 100})
    monkeypatch.setattr(restore, "archive_bytes", lambda artifact: b"synthetic")
    monkeypatch.setattr(restore, "archive_json", lambda payload, filename: {**proof(), "signature": "0" * 64})
    with pytest.raises(ValueError): restore.reconciliation(200)


def test_conflicting_signed_historical_names_fail_without_rewriting_journal(environment, monkeypatch):
    manager, _ = environment
    changed = receipts(manager)
    changed["runs"][RUN]["documents"][0]["filename"] = signed_filename(baseline.OWNER, "b" * 32)
    changed["signature"] = manager._signature({key: value for key, value in changed.items() if key != "signature"})
    before = manager.manifest_path.read_bytes()
    history(monkeypatch, [run(150), run(160)], {150: receipts(manager), 160: changed})
    with pytest.raises(restore.ReleaseError, match="Conflicting"): restore.restore(manager, 200, 100, CUTOFF)
    assert manager.manifest_path.read_bytes() == before


def test_failed_admission_still_preserves_a_private_signed_journal(environment, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    with pytest.raises(restore.ReleaseError): restore.main()
    path = tmp_path / ".ci_smoke_manifest.json"
    assert path.stat().st_mode & 0o077 == 0
    assert FixtureManager(path, "f" * 32).load_manifest() == {"manifest_version": 2, "runs": {}}
