"""Release admission is fail-closed; all HTTP, credentials and subprocesses synthetic."""

import hashlib
import io
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_gate as gate


@pytest.fixture
def render(monkeypatch):
    key = "synthetic-release-provider-key"
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    monkeypatch.setenv("RENDER_API_KEY", "synthetic-render-key")
    monkeypatch.setenv("RELEASE_COHERE_TRIAL_SHA256", fingerprint)
    service = {"id": gate.SERVICE, "ownerId": gate.WORKSPACE, "type": "web_service", "repo": "https://github.com/" + gate.REPOSITORY,
        "branch": "main", "autoDeploy": "no", "serviceDetails": {"plan": "free", "runtime": "docker", "url": gate.API_URL}}
    values = {"COHERE_API_KEY": key, "COHERE_TRIAL_KEY_SHA256": fingerprint,
        "COHERE_APPROVED_DATA_CLASSIFICATIONS": '["public","synthetic"]', "STORAGE_BACKEND": "supabase",
        "SUPABASE_URL": "https://" + gate.PROJECT + ".supabase.co"}
    calls = []
    def request(base, path, token=None, payload=None):
        calls.append((base, path, payload))
        return [{"envVar": {"key": key, "value": value}} for key, value in values.items()] if "env-vars" in path else service
    monkeypatch.setattr(gate, "request", request)
    return service, values, calls


def test_missing_configuration_fails_without_exposing_values(monkeypatch):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    with pytest.raises(gate.ReleaseError, match="RENDER_API_KEY"):
        gate.render_preflight()


def test_preflight_accepts_only_approved_existing_free_service(render):
    gate.render_preflight()
    assert all(payload is None for _, _, payload in render[2])


@pytest.mark.parametrize("change", ["paid-plan", "wrong-workspace", "autodeploy", "paid-key", "sensitive-content", "quota", "local-model", "worker-disabled"])
def test_invalid_platform_or_provider_configuration_cannot_start_deployment(render, change):
    service, values, calls = render
    if change == "paid-plan": service["serviceDetails"]["plan"] = "starter"
    if change == "wrong-workspace": service["ownerId"] = "another-workspace"
    if change == "autodeploy": service["autoDeploy"] = "yes"
    if change == "paid-key": values["COHERE_API_KEY"] = "different-unapproved-key"
    if change == "sensitive-content": values["COHERE_APPROVED_DATA_CLASSIFICATIONS"] = '["public","synthetic","medical"]'
    if change == "quota": values["COHERE_MONTHLY_CALL_LIMIT"] = "1001"
    if change == "local-model": values["EMBEDDING_MODEL"] = "all-MiniLM-L6-v2"
    if change == "worker-disabled": values["RUN_INGESTION_WORKER"] = "false"
    with pytest.raises(gate.ReleaseError): gate.deploy("a" * 40)
    assert all(payload is None for _, _, payload in calls)


@pytest.mark.parametrize("bad", ["a" * 7, "A" * 40, "main"])
def test_release_requires_exact_sha(bad):
    with pytest.raises(gate.ReleaseError): gate.trusted_ci(bad)


def test_ci_requires_all_successful_exact_main_jobs(monkeypatch):
    sha = "a" * 40
    monkeypatch.setenv("GH_TOKEN", "synthetic-github-token")
    monkeypatch.setattr(gate.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0)))
    jobs = [{"name": name, "status": "completed", "conclusion": "success"} for name in gate.REQUIRED_JOBS]
    def request(base, path, token=None):
        return {"jobs": jobs} if "/jobs" in path else {"workflow_runs": [{"id": 42, "head_sha": sha,
            "head_branch": "main", "event": "push", "conclusion": "success", "status": "completed"}]}
    monkeypatch.setattr(gate, "request", request)
    gate.trusted_ci(sha)
    jobs[0]["conclusion"] = "skipped"
    with pytest.raises(gate.ReleaseError): gate.trusted_ci(sha)


@pytest.mark.parametrize("url", ["", "postgresql://synthetic:synthetic@localhost/test", "postgresql://postgres.wrong-project@pooler.supabase.com/postgres",
    "postgresql://postgres." + gate.PROJECT + "@untrusted.example/postgres"])
def test_migrations_reject_other_databases(url):
    with pytest.raises(gate.ReleaseError): gate.validate_database_url(url)


def test_migration_failure_does_not_print_driver_output(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://postgres." + gate.PROJECT + ":synthetic@aws-synthetic.pooler.supabase.com/postgres")
    monkeypatch.setattr(gate.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=1, stdout=b"PRIVATE DRIVER DETAIL", stderr=b"PRIVATE DRIVER SECRET")))
    with pytest.raises(gate.ReleaseError) as error: gate.migrate()
    assert "PRIVATE" not in str(error.value) + capsys.readouterr().out


def test_deployment_is_pinned_and_serving_sha_is_verified(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "synthetic-render-token")
    monkeypatch.setattr(gate, "render_preflight", Mock())
    sha, calls = "b" * 40, []
    def request(base, path, token=None, payload=None):
        calls.append((base, path, payload))
        if payload is not None: return {"id": "dep-synthetic42"}
        if path == "/health": return {"git_commit": sha}
        return {"status": "live", "commit": {"id": sha}}
    monkeypatch.setattr(gate, "request", request)
    gate.deploy(sha)
    assert calls[0][2]["commitId"] == sha
    assert calls[-1][:2] == (gate.API_URL, "/health")


def test_reads_retry_503_but_deploy_post_is_not_repeated(monkeypatch):
    monkeypatch.setattr(gate.time, "sleep", lambda seconds: None)
    attempts = []
    def fail(request, timeout):
        attempts.append(request)
        raise urllib.error.HTTPError("https://api.render.com", 503, "Synthetic outage", {}, io.BytesIO(b"PRIVATE BODY"))
    monkeypatch.setattr(gate.urllib.request, "urlopen", fail)
    with pytest.raises(gate.ReleaseError) as error: gate.request("https://api.render.com", "/synthetic")
    assert len(attempts) == 3 and "PRIVATE" not in str(error.value)
    attempts.clear()
    with pytest.raises(gate.ReleaseError): gate.request("https://api.render.com", "/synthetic", payload={"commitId": "a" * 40})
    assert len(attempts) == 1


@pytest.mark.parametrize("backend", ["qdrant", "unknown", "supabse", ""])
def test_standalone_release_rejects_legacy_vector_backends_before_deploy(render, backend):
    render[1]["VECTOR_STORE_BACKEND"] = backend
    with pytest.raises(gate.ReleaseError, match="PostgreSQL/pgvector"):
        gate.render_preflight()
    assert all(payload is None for _, _, payload in render[2])
