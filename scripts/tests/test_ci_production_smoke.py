"""Offline regression tests for the real-HTTP production smoke runner.

These never contact the production backend.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure repo root and scripts directory are available on sys.path
repo_root = Path(__file__).resolve().parents[2]
scripts_dir = Path(__file__).resolve().parents[1]
for path_entry in (str(repo_root), str(scripts_dir)):
    if path_entry not in sys.path:
        sys.path.insert(0, path_entry)

try:
    from scripts import ci_production_smoke as smoke
except ModuleNotFoundError:
    import ci_production_smoke as smoke



@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    smoke.RESULTS.clear()
    for key in (
        "PROD_SMOKE_BEARER_TOKEN",
        "PROD_SMOKE_DOCUMENT_ID",
        "PROD_SMOKE_EXPECTED_MARKER",
        "REQUIRE_RAG",
        "RUN_LLM_ANSWER",
    ):
        monkeypatch.delenv(key, raising=False)


def test_memory_guard_rejects_high_memory():
    smoke.assert_memory({"memory_mb": 295, "pressure_level": "normal"}, "memory")
    with pytest.raises(AssertionError, match="unsafe memory"):
        smoke.assert_memory({"memory_mb": 443, "pressure_level": "critical"}, "memory")


def test_backend_sha_allows_exact_match(monkeypatch):
    monkeypatch.setattr(smoke, "EXPECTED_SHA", "a" * 40)
    assert smoke.compatible_backend_sha("a" * 40)
    assert not smoke.compatible_backend_sha("dev")


def test_backend_sha_rejects_runtime_drift(monkeypatch):
    monkeypatch.setattr(smoke, "EXPECTED_SHA", "b" * 40)
    returns = iter([0, 1])

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], next(returns))

    monkeypatch.setattr(smoke.subprocess, "run", fake_run)
    assert not smoke.compatible_backend_sha("a" * 40)


def test_cors_validates_origin_and_method(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {"access-control-allow-origin": smoke.WEB, "access-control-allow-methods": "GET, POST"},
            {},
        ),
    )
    smoke.check_cors()
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (200, {"access-control-allow-origin": "*"}, {}),
    )
    with pytest.raises(AssertionError, match="origin"):
        smoke.check_cors()


def test_database_readiness_requires_real_connected_contract(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {},
            {"status": "ready", "database": "connected", "git_commit": "abcd"},
        ),
    )
    smoke.ready_check("abcd")
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"status": "ok"}))
    monkeypatch.setattr(smoke.time, "sleep", lambda *_: None)
    with pytest.raises(AssertionError, match="Database readiness"):
        smoke.ready_check("abcd")


def test_unauthenticated_queries_must_be_rejected(monkeypatch):
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (401, {}, {}))
    smoke.check_auth_boundary()
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {}))
    with pytest.raises(AssertionError, match="Document authorization"):
        smoke.check_auth_boundary()


def test_rag_missing_secrets_is_explicitly_not_run(monkeypatch):
    smoke.run_real_search()
    assert any("NOT RUN" in result for result in smoke.RESULTS)
    monkeypatch.setenv("REQUIRE_RAG", "true")
    with pytest.raises(AssertionError, match="required"):
        smoke.run_real_search()


def test_real_search_verifies_fixture_marker(monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_BEARER_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("PROD_SMOKE_DOCUMENT_ID", "synthetic-doc")
    monkeypatch.setenv("PROD_SMOKE_EXPECTED_MARKER", "SS-SYNTHETIC-SMOKE-42")

    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {},
            {
                "query": "What is the unique CI verification code in the synthetic test document?",
                "results": [
                    {
                        "document_id": "synthetic-doc",
                        "content": "The code is SS-SYNTHETIC-SMOKE-42.",
                    }
                ],
            },
        ),
    )
    smoke.run_real_search()
    assert any("Real semantic search" in result for result in smoke.RESULTS)


def test_real_search_rejects_empty_results(monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_BEARER_TOKEN", "synthetic-test-token")
    monkeypatch.setenv("PROD_SMOKE_DOCUMENT_ID", "synthetic-doc")
    monkeypatch.setenv("PROD_SMOKE_EXPECTED_MARKER", "SS-SYNTHETIC-SMOKE-42")
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"results": []}))
    with pytest.raises(AssertionError, match="no retrieved"):
        smoke.run_real_search()
