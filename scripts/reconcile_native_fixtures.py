"""Protected read-only operator audit; signing a baseline never mutates app data."""
import asyncio
import hashlib
import json
import os
import re
import stat
from pathlib import Path

from recover_missing_vectors import write_private_plan
from release_gate import (
    API_URL,
    REPOSITORY,
    ReleaseError,
    request,
    require,
    trusted_ci,
    validate_database_url,
)
from smoke_recovery_baseline import seal_baseline


def admission():
    if (require("GITHUB_ACTIONS"), require("GITHUB_REPOSITORY"), require("GITHUB_REF"), require("GITHUB_EVENT_NAME")) != (
        "true", REPOSITORY, "refs/heads/main", "workflow_dispatch"):
        raise ReleaseError("Trusted main operator dispatch required.")
    if require("GITHUB_WORKFLOW_REF") != REPOSITORY + "/.github/workflows/native-fixture-reconciliation.yml@refs/heads/main":
        raise ReleaseError("Trusted reconciliation workflow required.")
    run_id = require("GITHUB_RUN_ID")
    if not re.fullmatch(r"[1-9][0-9]{0,19}", run_id):
        raise ReleaseError("Trusted operator run identity required.")
    trusted_ci(require("GITHUB_SHA"))
    trusted_ci(require("EXPECTED_SHA"))
    print("CHECK no active predecessor can mutate after the historical cutoff", flush=True)
    # A prior release queued before this audit could mutate after the snapshot.
    # Reject all active predecessor states before establishing a historical cutoff.
    for workflow in ("release.yml", "post-deploy-smoke.yml"):
        for status in ("queued", "in_progress", "waiting", "pending", "requested"):
            result = request("https://api.github.com", "/repos/" + REPOSITORY + "/actions/workflows/" + workflow
                + "/runs?branch=main&event=workflow_dispatch&status=" + status + "&per_page=100", require("GH_TOKEN"))
            if (not isinstance(result, dict) or type(result.get("total_count")) is not int
                or not 0 <= result["total_count"] < 100 or not isinstance(result.get("workflow_runs"), list)
                or len(result["workflow_runs"]) != result["total_count"]):
                raise ReleaseError("Native predecessor admission is unavailable or exceeds its bound.")
            for run in result["workflow_runs"]:
                if (not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] <= 0
                    or run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
                    or run.get("status") != status):
                    raise ReleaseError("Untrusted native predecessor metadata.")
                if run["id"] <= int(run_id):
                    raise ReleaseError("Prior native release is still active; wait for its cleanup provenance.")
    print("CHECK exact serving revision and SQL readiness", flush=True)
    for path, status in (("/health", "ok"), ("/ready", "ready")):
        value = request(API_URL, path)
        if value.get("git_commit") != require("EXPECTED_SHA") or value.get("status") != status:
            raise ReleaseError("Exact serving revision/readiness required.")
        if path == "/ready" and value.get("database") != "connected":
            raise ReleaseError("Serving database readiness required.")
    url = require("DATABASE_URL")
    validate_database_url(url)
    root = Path(require("RUNNER_TEMP"))
    output = Path(require("NATIVE_BASELINE_PATH"))
    if not root.is_absolute() or output != root / "native-reconciliation" / "baseline.json":
        raise ReleaseError("Private runner baseline path required.")
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = output.parent.lstat()
    if (not stat.S_ISDIR(info.st_mode) or output.parent.is_symlink()
        or info.st_uid != os.geteuid() or info.st_mode & 0o077 or output.exists() or output.is_symlink()):
        raise ReleaseError("Private exclusive baseline destination required.")
    return url, output


async def audit(url, key):
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from src.services.release_scope_audit import (
        ReleaseScopeNotEmptyError,
        audit_native_release_scope,
    )

    target = make_url(url).set(drivername="postgresql+asyncpg")
    fingerprint = hashlib.sha256(json.dumps([target.host, target.port or 5432, target.database, target.username],
        separators=(",", ":")).encode()).hexdigest()
    engine = create_async_engine(target, hide_parameters=True, isolation_level="REPEATABLE READ",
        connect_args={"timeout": 15, "command_timeout": 15}, pool_size=1, max_overflow=0)
    try:
        async with AsyncSession(engine) as db, db.begin():
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '15s'"))
            await db.execute(text("SET LOCAL lock_timeout = '5s'"))
            try:
                proof = await audit_native_release_scope(db, key)
            except ReleaseScopeNotEmptyError as error:
                # This owned exception contains only fixed stage labels, not SQL details.
                print("FAIL dedicated synthetic scope audit: " + str(error), flush=True)
                raise
            return proof, fingerprint
    finally:
        await engine.dispose()


def main():
    url, output = admission()
    key = require("RELEASE_SMOKE_KEY")
    if len(key.encode()) < 32:
        raise ReleaseError("Protected fixture ownership key required.")
    print("CHECK repeatable-read, read-only dedicated SQL and Storage metadata", flush=True)
    proof, target = asyncio.run(audit(url, key))
    final_health = request(API_URL, "/health")
    if final_health.get("git_commit") != require("EXPECTED_SHA") or final_health.get("status") != "ok":
        raise ReleaseError("Serving revision changed during reconciliation.")
    baseline = seal_baseline(proof, require("GITHUB_RUN_ID"), require("GITHUB_SHA"),
        require("EXPECTED_SHA"), target, key)
    write_private_plan(output, baseline)
    print("PASS read-only dedicated synthetic scope audit; private signed baseline saved")


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- external SQL/platform details must remain redacted
        # External SQL/platform errors can include credentials or query parameters.
        raise SystemExit("FAIL native reconciliation; verify trusted CI, fixture ownership and empty private scope.") from None
