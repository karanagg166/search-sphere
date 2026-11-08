"""Restore signed native cleanup provenance before a fresh runner can upload."""
import hmac
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from release_artifacts import (
    archive_bytes,
    archive_json,
    artifact_for,
    github,
    workflow_runs,
)
from release_gate import REPOSITORY, ReleaseError, require, trusted_ci
from smoke_fixtures import (
    FixtureManager,
    verified_conversation_title,
    verified_filename,
)
from smoke_recovery_baseline import OWNER, verify_baseline

WORKFLOWS = {
    "release.yml": ("standalone-release-fixture-receipts-",
        "Upload, index, retrieve, verify standalone/conversation answers and SSE, and clean up"),
    "post-deploy-smoke.yml": ("standalone-fixture-receipts-",
        "Verify upload, indexing, standalone/conversation answers, SSE, and cleanup"),
}


def timestamp(value):
    if not isinstance(value, str):
        raise ReleaseError("Native history timestamp is unavailable.")
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is None or result > datetime.now(UTC) + timedelta(minutes=5):
            raise ValueError("untrusted time")
        return result
    except ValueError:
        raise ReleaseError("Native history timestamp is invalid.") from None


def admission():
    if (require("GITHUB_ACTIONS"), require("GITHUB_REPOSITORY"), require("GITHUB_REF"), require("GITHUB_EVENT_NAME")) != (
        "true", REPOSITORY, "refs/heads/main", "workflow_dispatch"):
        raise ReleaseError("Trusted main native release dispatch required.")
    if require("GITHUB_WORKFLOW_REF") not in {
        REPOSITORY + "/.github/workflows/" + workflow + "@refs/heads/main" for workflow in WORKFLOWS
    }:
        raise ReleaseError("Trusted native release workflow required.")
    run_id = require("GITHUB_RUN_ID")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise ReleaseError("Trusted native run identity required.")
    trusted_ci(require("GITHUB_SHA"))
    trusted_ci(require("EXPECTED_SHA"))
    return int(run_id)


def reconciliation(current_id):
    candidates = sorted((run for run in workflow_runs("native-fixture-reconciliation.yml")
        if run.get("status") == "completed" and run.get("conclusion") == "success"),
        key=lambda run: run["id"], reverse=True)
    if not candidates or candidates[0]["id"] >= current_id:
        raise ReleaseError("A fresh native dispatch after signed operator reconciliation is required.")
    run = candidates[0]
    artifact = artifact_for(run, "native-fixture-reconciliation-" + str(run["id"]))
    if artifact is None:
        raise ReleaseError("Signed operator baseline is missing; run read-only reconciliation again.")
    proof = verify_baseline(archive_json(archive_bytes(artifact), "baseline.json"), require("PROD_SMOKE_FIXTURE_KEY"))
    if proof["through_run_id"] != str(run["id"]) or proof["source_sha"] != run.get("head_sha"):
        raise ReleaseError("Operator baseline does not belong to its trusted workflow revision.")
    cutoff = timestamp(proof["created_at"])
    if not timestamp(run.get("created_at")) <= cutoff <= timestamp(run.get("updated_at")) + timedelta(seconds=5):
        raise ReleaseError("Operator baseline timestamp is outside its workflow run.")
    trusted_ci(proof["source_sha"])
    trusted_ci(proof["api_sha"])
    return run["id"], cutoff


def mutation_was_skipped(run, workflow):
    data = github(f"/actions/runs/{run['id']}/jobs?filter=latest&per_page=100")
    if not isinstance(data, dict):
        return False
    count, jobs = data.get("total_count"), data.get("jobs")
    if type(count) is not int or not 0 <= count < 100 or not isinstance(jobs, list) or len(jobs) != count:
        return False
    steps = [step for job in jobs if isinstance(job, dict) for step in job.get("steps", [])
        if isinstance(step, dict) and step.get("name") == WORKFLOWS[workflow][1]]
    return len(steps) == 1 and steps[0].get("status") == "completed" and steps[0].get("conclusion") == "skipped"


def pending_receipts(value, manager):
    if not isinstance(value, dict) or set(value) != {"manifest_version", "runs", "signature"}:
        raise ReleaseError("Signed version-two native receipts are required.")
    body = {key: item for key, item in value.items() if key != "signature"}
    signature = value["signature"]
    manager._validate(body)
    if type(body["manifest_version"]) is not int:
        raise ReleaseError("Native receipt version is invalid.")
    if (not isinstance(signature, str) or not re.fullmatch(r"[a-f0-9]{64}", signature)
        or not hmac.compare_digest(signature, manager._signature(body))):
        raise ReleaseError("Native cleanup receipt signature is invalid.")
    pending = {}
    for run_id, entry in body["runs"].items():
        if (not re.fullmatch(r"[a-f0-9]{32}", run_id) or entry.get("run_id") != run_id
            or entry.get("user_id") != OWNER):
            raise ReleaseError("Native cleanup receipt owner or run is invalid.")
        for kind, id_field, name_field, verify in (
            ("documents", "doc_id", "filename", verified_filename),
            ("conversations", "conversation_id", "title", verified_conversation_title),
        ):
            for row in entry.get(kind, []):
                identity, name = row.get(id_field), row.get(name_field)
                if (not isinstance(identity, str) or str(uuid.UUID(identity)) != identity
                    or not isinstance(name, str) or not verify(name, OWNER)
                    or row.get("status") not in {"pending_cleanup", "failed_cleanup", "cleaned_up"}):
                    raise ReleaseError("Native cleanup resource identity or provenance is invalid.")
                if row["status"] == "cleaned_up":
                    continue
                key = (kind, identity)
                if key in pending and pending[key] != name:
                    raise ReleaseError("Conflicting signed native resource identities.")
                pending[key] = name
    return pending


def restore(manager, current_id, baseline_id, cutoff):
    existing = manager.load_manifest()
    pending = pending_receipts({**existing, "signature": manager._signature(existing)}, manager)
    for workflow, (prefix, _) in WORKFLOWS.items():
        print("CHECK restore bounded signed native workflow receipts: " + workflow, flush=True)
        for run in workflow_runs(workflow):
            if run["id"] >= current_id:
                continue
            updated = timestamp(run.get("updated_at"))
            # An old run ID can be rerun after reconciliation. Only an old,
            # completed attempt whose update predates the snapshot is covered.
            if run["id"] <= baseline_id and run.get("status") == "completed" and updated <= cutoff:
                continue
            if run.get("status") != "completed":
                raise ReleaseError("A prior native attempt is still active; wait before a fresh dispatch.")
            artifact = artifact_for(run, prefix + str(run["id"]))
            if artifact is None:
                if mutation_was_skipped(run, workflow):
                    continue
                raise ReleaseError("Native mutation lacks signed receipts; run read-only reconciliation.")
            rows = pending_receipts(archive_json(archive_bytes(artifact), ".ci_smoke_manifest.json"), manager)
            for key, name in rows.items():
                if key in pending and pending[key] != name:
                    raise ReleaseError("Conflicting historical native cleanup provenance.")
                pending[key] = name
            if len(pending) > manager.MAX_ENTRIES:
                raise ReleaseError("Native receipt capacity requires read-only reconciliation.")
    # Pending wins over a later copied 'cleaned' row. The authenticated smoke
    # independently re-verifies every retained resource's physical cleanup.
    for (kind, identity), name in pending.items():
        if kind == "documents":
            manager.register_fixture(identity, name, "", OWNER)
        else:
            manager.register_conversation(identity, name, OWNER)
    print(f"PASS restored {len(pending)} signed pending native cleanup resources", flush=True)


def main():
    manager = FixtureManager(Path(".ci_smoke_manifest.json"), uuid.uuid4().hex)
    # Keep a private signed journal even when admission/health later fails.
    manager.save_manifest(manager.load_manifest())
    current_id = admission()
    print("CHECK trusted signed operator reconciliation before native mutation", flush=True)
    baseline_id, cutoff = reconciliation(current_id)
    restore(manager, current_id, baseline_id, cutoff)


if __name__ == "__main__":
    try:
        main()
    except ReleaseError as error:
        raise SystemExit("FAIL " + str(error)) from None
    except Exception:  # noqa: BLE001 -- archive/platform details must never expose private content
        raise SystemExit("FAIL native receipt verification; require trusted signed operator reconciliation.") from None
