"""Trusted-main release admission, redacted migrations and pinned free Render deploy."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

REPOSITORY = "karanagg166/search-sphere"
SERVICE = "srv-db02odm7bikc73fv0e7g"
WORKSPACE = "tea-d502qthr0fns73a09d0g"
API_URL = "https://search-sphere-api.onrender.com"
PROJECT = "jyzjgoniuwexvipbbpwa"
REQUIRED_JOBS = {"Backend Lint & Tests", "Worker Ingestion & Extraction Tests", "Hosted Runtime Image", "Frontend Lint, Test & Build"}


class ReleaseError(RuntimeError):
    pass


def require(name):
    value = os.environ.get(name, "")
    if not value:
        raise ReleaseError("Required protected configuration is missing: " + name)
    return value


def request(base, path, token=None, payload=None):
    headers = {"Accept": "application/json", "User-Agent": "SearchSphereReleaseGate/1"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=25) as response:
                return json.loads(response.read(4 * 1024 * 1024))
        except urllib.error.HTTPError as error:
            if data is None and error.code in {429, 500, 502, 503, 504} and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise ReleaseError(f"Release HTTP operation failed with status {error.code}.") from None
        except (OSError, ValueError) as error:
            if data is None and isinstance(error, (TimeoutError, urllib.error.URLError)) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise ReleaseError("Release HTTP operation failed (" + type(error).__name__ + ").") from None


def trusted_ci(sha):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ReleaseError("A full lowercase main revision is required.")
    if subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True, check=False).returncode:
        raise ReleaseError("Intended revision is not an ancestor of trusted main.")
    token = require("GH_TOKEN")
    root = "/repos/" + REPOSITORY
    runs = request("https://api.github.com", root + "/actions/workflows/ci.yml/runs?head_sha=" + sha + "&branch=main&event=push&per_page=20", token)
    for run in runs.get("workflow_runs", []):
        if (run.get("head_sha"), run.get("head_branch"), run.get("event"), run.get("conclusion"), run.get("status")) != (
            sha, "main", "push", "success", "completed"):
            continue
        jobs = request("https://api.github.com", root + f"/actions/runs/{int(run['id'])}/jobs?per_page=100", token)
        successful = {job["name"] for job in jobs.get("jobs", []) if job.get("status") == "completed" and job.get("conclusion") == "success"}
        if REQUIRED_JOBS.issubset(successful):
            print(json.dumps({"verified_main_ci": int(run["id"]), "revision": sha}))
            return
    raise ReleaseError("The exact main revision lacks successful required pre-deployment CI jobs.")


def render_preflight():
    token = require("RENDER_API_KEY")
    base = "https://api.render.com/v1"
    service = request(base, "/services/" + SERVICE, token)
    details = service.get("serviceDetails", {})
    if (service.get("id"), service.get("ownerId"), service.get("type"), service.get("repo"), service.get("branch")) != (
        SERVICE, WORKSPACE, "web_service", "https://github.com/" + REPOSITORY, "main"):
        raise ReleaseError("Render service identity does not match the authorized release target.")
    if details.get("plan") != "free" or details.get("runtime") != "docker" or details.get("url") != API_URL or service.get("autoDeploy") != "no":
        raise ReleaseError("Release requires the existing free Docker service with automatic deployment disabled.")
    values, cursor = {}, ""
    for _ in range(4):
        page = request(base, f"/services/{SERVICE}/env-vars?limit=100" + ("&cursor=" + cursor if cursor else ""), token)
        for item in page:
            entry = item["envVar"]
            values[entry["key"]] = entry["value"]
        if len(page) < 100:
            break
        cursor = page[-1]["cursor"]
    else:
        raise ReleaseError("Render environment enumeration exceeded the bounded release check.")
    fingerprint = require("RELEASE_COHERE_TRIAL_SHA256")
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or hashlib.sha256(values.get("COHERE_API_KEY", "").encode()).hexdigest() != fingerprint:
        raise ReleaseError("Render's Cohere credential does not match the owner-approved trial fingerprint.")
    if values.get("COHERE_TRIAL_KEY_SHA256") != fingerprint:
        raise ReleaseError("Render's free-trial approval fingerprint is not configured.")
    if json.loads(values.get("COHERE_APPROVED_DATA_CLASSIFICATIONS", "[]")) != ["public", "synthetic"] or values.get("COHERE_SENSITIVE_APPROVAL_REF"):
        raise ReleaseError("Trial inference must remain restricted to public and synthetic content.")
    expected = {"EMBEDDING_MODEL": "embed-english-light-v3.0", "SEMANTIC_CHUNKING_MODEL": "embed-english-light-v3.0",
        "RERANKER_MODEL_NAME": "rerank-v3.5", "RAG_GENERATION_MODEL": "command-a-03-2025",
        "QUERY_REWRITE_MODEL": "command-a-03-2025", "IMAGE_CAPTION_MODEL": "command-a-vision-07-2025"}
    if any(values.get(key, value) != value for key, value in expected.items()):
        raise ReleaseError("Render model configuration differs from the verified hosted model versions.")
    if not 0 < int(values.get("COHERE_MONTHLY_CALL_LIMIT", "900")) <= 900 or values.get("RUN_INGESTION_WORKER", "true").lower() != "true":
        raise ReleaseError("The free-tier quota cap and in-process durable worker are required.")
    if values.get("STORAGE_BACKEND") != "supabase" or (values.get("SUPABASE_URL") or values.get("NEXT_PUBLIC_SUPABASE_URL")) != f"https://{PROJECT}.supabase.co":
        raise ReleaseError("Render storage configuration does not match the authorized Supabase project.")
    print(json.dumps({"render_service": SERVICE, "workspace": WORKSPACE, "plan": "free", "provider_policy_verified": True}))


def validate_database_url(url):
    parsed = urlsplit(url)
    authorized_host = parsed.hostname == f"db.{PROJECT}.supabase.co" or (
        (parsed.hostname or "").endswith(".pooler.supabase.com") and (parsed.username or "").endswith("." + PROJECT))
    if parsed.scheme not in {"postgresql+asyncpg", "postgresql", "postgres"} or not authorized_host:
        raise ReleaseError("Release migrations require the authorized Supabase project SQL connection.")


async def verify_database():
    import src.models  # noqa: F401
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from src.db import Base, engine

    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    try:
        async with engine.connect() as connection:
            actual = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if actual != expected:
                raise ReleaseError("Production migration head does not match the selected revision.")
            exposed = await connection.scalar(text("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname=ANY(CAST(:tables AS text[])) AND
                (NOT c.relrowsecurity OR has_table_privilege('anon',c.oid,'SELECT,INSERT,UPDATE,DELETE')
                 OR has_table_privilege('authenticated',c.oid,'SELECT,INSERT,UPDATE,DELETE')
                 OR has_any_column_privilege('anon',c.oid,'SELECT,INSERT,UPDATE')
                 OR has_any_column_privilege('authenticated',c.oid,'SELECT,INSERT,UPDATE'))"""), {"tables": list(Base.metadata.tables)})
            if exposed:
                raise ReleaseError("Backend tables retain unintended browser privileges or lack RLS.")
            if await connection.scalar(text("SELECT public FROM storage.buckets WHERE id='documents'")) is not False:
                raise ReleaseError("The private original-document bucket check failed.")
        print(json.dumps({"migration_head": expected, "browser_table_access_denied": True, "private_bucket": True}))
    finally:
        await engine.dispose()


def migrate():
    validate_database_url(require("DATABASE_URL"))
    result = subprocess.run(["alembic", "upgrade", "head"], capture_output=True, timeout=180, check=False)
    if result.returncode:
        raise ReleaseError("Production migration failed; captured driver output was withheld to protect credentials.")
    asyncio.run(verify_database())


async def seed_identity():
    from src.db import AsyncSessionLocal, engine
    from src.services.release_identity import provision_release_identity

    try:
        async with AsyncSessionLocal() as db, db.begin():
            await provision_release_identity(db, require("RELEASE_SMOKE_EMAIL"), require("RELEASE_SMOKE_PASSWORD"), require("RELEASE_SMOKE_KEY"))
        print(json.dumps({"synthetic_release_identity_ready": True, "provider_scope": "dedicated-synthetic-account-only"}))
    finally:
        await engine.dispose()


def deploy(sha):
    render_preflight()
    token = require("RENDER_API_KEY")
    base, path = "https://api.render.com/v1", f"/services/{SERVICE}/deploys"
    deployment = request(base, path, token, {"commitId": sha, "clearCache": "do_not_clear"})
    identifier = deployment.get("id", "")
    if not re.fullmatch(r"dep-[a-z0-9]+", identifier):
        raise ReleaseError("Render did not return a valid pinned deployment identity.")
    print(json.dumps({"deployment": identifier, "revision": sha}), flush=True)
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        state = request(base, path + "/" + identifier, token)
        if state.get("status") == "live":
            if state.get("commit", {}).get("id") != sha:
                raise ReleaseError("Render reports a different deployed commit.")
            for _ in range(30):
                health = request(API_URL, "/health")
                if health.get("git_commit") == sha:
                    print(json.dumps({"deployment": identifier, "live_revision": sha}))
                    return
                time.sleep(10)
            raise ReleaseError("The expected live revision was not observed after deployment.")
        if state.get("status") not in {"created", "build_in_progress", "pre_deploy_in_progress", "update_in_progress", "queued"}:
            raise ReleaseError("Pinned deployment did not succeed: " + str(state.get("status")))
        time.sleep(10)
    raise ReleaseError("Pinned deployment exceeded the bounded rollout wait.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["preflight", "migrate", "seed", "deploy"])
    phase = parser.parse_args().phase
    sha = require("EXPECTED_SHA")
    trusted_ci(sha)
    if phase == "migrate":
        migrate()
    elif phase == "seed":
        validate_database_url(require("DATABASE_URL"))
        asyncio.run(seed_identity())
    elif phase == "deploy":
        deploy(sha)
    else:
        render_preflight()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 -- credential-safe CLI boundary
        # Never print opaque provider bodies, environment values or driver traces.
        message = str(error) if isinstance(error, ReleaseError) else type(error).__name__
        raise SystemExit("Release gate failed: " + message) from None
