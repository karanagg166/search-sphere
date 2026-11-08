"""Protected read-only operator audit; signing a baseline never mutates app data."""
import asyncio
import hashlib
import json
import os
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
    trusted_ci(require("GITHUB_SHA"))
    trusted_ci(require("EXPECTED_SHA"))
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
    from src.services.release_scope_audit import audit_native_release_scope

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
            proof = await audit_native_release_scope(db, key)
            return proof, fingerprint
    finally:
        await engine.dispose()


def main():
    url, output = admission()
    key = require("RELEASE_SMOKE_KEY")
    if len(key.encode()) < 32:
        raise ReleaseError("Protected fixture ownership key required.")
    proof, target = asyncio.run(audit(url, key))
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
