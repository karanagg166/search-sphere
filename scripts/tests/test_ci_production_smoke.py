"""Offline regression tests for the real-HTTP production smoke runner.

These never contact the production backend.
"""
import io
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
        "PROD_SMOKE_USER_EMAIL",
        "PROD_SMOKE_USER_PASSWORD",
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


def test_generate_synthetic_pdf():
    marker = "SS-SYNTH-MARKER-999"
    pdf = smoke.generate_synthetic_pdf(marker, doc_name="Test-Doc")
    assert pdf.startswith(b"%PDF-1.4")
    assert marker.encode("latin-1") in pdf
    assert b"xref" in pdf
    assert b"trailer" in pdf
    assert pdf.rstrip().endswith(b"%%EOF")


def test_acquire_ci_auth_uses_token_if_set(monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_BEARER_TOKEN", "bearer_token_xyz")
    auth = smoke.acquire_ci_auth()
    assert auth == ("bearer_token_xyz", "")


def test_acquire_ci_auth_logs_in_with_email_password(monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_USER_EMAIL", "test@search-sphere.internal")
    monkeypatch.setenv("PROD_SMOKE_USER_PASSWORD", "Pass123!")

    def fake_http(url, **kwargs):
        if "/auth/login" in url:
            return 200, {}, {"access_token": "token_abc", "user": {"id": "user-123"}}
        return 404, {}, {}

    monkeypatch.setattr(smoke, "http", fake_http)
    auth = smoke.acquire_ci_auth()
    assert auth == ("token_abc", "user-123")


def test_acquire_ci_auth_signs_up_when_login_fails(monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_USER_EMAIL", "test@search-sphere.internal")
    monkeypatch.setenv("PROD_SMOKE_USER_PASSWORD", "Pass123!")

    def fake_http(url, **kwargs):
        if "/auth/login" in url:
            return 401, {}, {"detail": "No account found"}
        if "/auth/signup" in url:
            return 201, {}, {"access_token": "token_signup", "user": {"id": "user-signup-456"}}
        return 404, {}, {}

    monkeypatch.setattr(smoke, "http", fake_http)
    auth = smoke.acquire_ci_auth()
    assert auth == ("token_signup", "user-signup-456")


def test_ensure_synthetic_document_polls_until_indexed(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda *_: None)
    upload_called = False

    def fake_upload(*args, **kwargs):
        nonlocal upload_called
        upload_called = True
        return 201, {}, {"id": "doc-synth-001"}

    statuses = iter(["pending", "indexed"])

    def fake_http(url, **kwargs):
        return 200, {}, {"status": next(statuses), "chunk_count": 2}

    monkeypatch.setattr(smoke, "http_upload", fake_upload)
    monkeypatch.setattr(smoke, "http", fake_http)

    doc_id, was_created = smoke.ensure_synthetic_document("token_xyz", "MARKER-123")
    assert upload_called
    assert doc_id == "doc-synth-001"
    assert was_created is True


def test_cleanup_synthetic_document(monkeypatch):
    deleted = False

    def fake_http(url, **kwargs):
        nonlocal deleted
        if kwargs.get("method") == "DELETE":
            deleted = True
            return 204, {}, {}
        return 404, {}, {}

    monkeypatch.setattr(smoke, "http", fake_http)
    smoke.cleanup_synthetic_document("token_xyz", "doc-synth-001")
    assert deleted
    assert any("purged synthetic document" in r for r in smoke.RESULTS)


def test_read_sse_stream(monkeypatch):
    sse_data = (
        b"event: metadata\r\n"
        b'data: {"query": "What is the code?", "rewritten": false}\r\n\r\n'
        b"event: source\r\n"
        b'data: {"sources": [{"document_id": "doc-1"}]}\r\n\r\n'
        b"event: token\r\n"
        b'data: {"token": "The"}\r\n\r\n'
        b"event: token\r\n"
        b'data: {"token": " code is 42."}\r\n\r\n'
        b"event: done\r\n"
        b'data: {"answer": "The code is 42."}\r\n\r\n'
    )
    fake_resp = io.BytesIO(sse_data)
    monkeypatch.setattr(smoke.urllib.request, "urlopen", lambda *args, **kwargs: fake_resp)

    events = smoke.read_sse_stream("http://mock/answer/stream", data={}, headers={})
    assert len(events) == 5
    assert events[0][0] == "metadata"
    assert events[1][0] == "source"
    assert events[2][0] == "token"
    assert events[3][0] == "token"
    assert events[4][0] == "done"
    assert events[4][1]["answer"] == "The code is 42."


def test_real_rag_pipeline_search_and_answer(monkeypatch):
    doc_id = "doc-test-synth"
    marker = "SS-SYNTH-MARKER"

    def fake_http(url, **kwargs):
        if url.endswith("/search"):
            return 200, {}, {
                "query": "What is the unique CI verification code in the synthetic test document?",
                "results": [
                    {
                        "document_id": doc_id,
                        "content": f"Found marker: {marker}",
                        "rerank_score": 0.854,
                    }
                ],
            }
        if url.endswith("/answer"):
            return 200, {}, {
                "answer": f"The verification code is {marker}.",
                "sources": [{"document_id": doc_id}],
            }
        return 404, {}, {}

    fake_sse = [
        ("metadata", {"query": "test"}),
        ("source", {"sources": [{"document_id": doc_id}]}),
        ("token", {"token": f"Code is {marker}"}),
        ("done", {"answer": f"Code is {marker}"}),
    ]
    monkeypatch.setattr(smoke, "http", fake_http)
    monkeypatch.setattr(smoke, "read_sse_stream", lambda *args, **kwargs: fake_sse)

    smoke.run_real_rag_pipeline("token_abc", doc_id, marker, do_answer=True)
    assert any("Real semantic search" in r for r in smoke.RESULTS)
    assert any("Real grounded answer" in r for r in smoke.RESULTS)
    assert any("SSE answer streaming" in r for r in smoke.RESULTS)
    assert any("Cross-encoder reranking verification" in r for r in smoke.RESULTS)


def test_check_diagnostics_detects_remote_qdrant_and_healthy_models(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {},
            {
                "database": "ok",
                "fastembed": "ok (384 dims, 4.2ms)",
                "cross_encoder": "ok (TextCrossEncoder, score=0.8921, 8.1ms)",
                "qdrant": "ok (remote, collection 'documents', 12 points)",
            },
        ),
    )
    smoke.check_diagnostics()
    assert any("Diagnostic probe" in r and "remote" in r for r in smoke.RESULTS)


def test_check_diagnostics_fails_on_embedded_fallback(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {},
            {
                "database": "ok",
                "fastembed": "ok",
                "cross_encoder": "ok",
                "qdrant": "ok (embedded, collection 'documents', 0 points)",
            },
        ),
    )
    with pytest.raises(AssertionError, match="ephemeral embedded fallback mode"):
        smoke.check_diagnostics()


def test_check_diagnostics_fails_on_cross_encoder_error(monkeypatch):
    monkeypatch.setattr(
        smoke,
        "http",
        lambda *args, **kwargs: (
            200,
            {},
            {
                "database": "ok",
                "fastembed": "ok",
                "cross_encoder": "error: Model cross-encoder/ms-marco-MiniLM-L-6-v2 is not supported",
                "qdrant": "ok (remote, collection 'documents', 12 points)",
            },
        ),
    )
    with pytest.raises(AssertionError, match="cross-encoder reranker error"):
        smoke.check_diagnostics()


def test_ensure_synthetic_document_rejects_zero_or_missing_chunk_count(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda *_: None)
    monkeypatch.setattr(smoke, "http_upload", lambda *args, **kwargs: (201, {}, {"id": "doc-001"}))

    # Test chunk_count = 0
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"status": "indexed", "chunk_count": 0}))
    with pytest.raises(AssertionError, match="chunk_count=0"):
        smoke.ensure_synthetic_document("token_xyz", "MARKER")

    # Test missing chunk_count
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"status": "indexed"}))
    with pytest.raises(AssertionError, match="chunk_count is missing"):
        smoke.ensure_synthetic_document("token_xyz", "MARKER")


def test_cleanup_synthetic_document_checks_vector_purge(monkeypatch):
    def fake_http(url, **kwargs):
        if kwargs.get("method") == "DELETE":
            return 204, {}, {}
        if url.endswith("/search"):
            return 200, {}, {"results": []}
        return 404, {}, {}

    monkeypatch.setattr(smoke, "http", fake_http)
    smoke.cleanup_synthetic_document("token_xyz", "doc-001", marker="MARKER-123")
    assert any("purged synthetic document" in r for r in smoke.RESULTS)
    assert any("Vector deletion verification" in r for r in smoke.RESULTS)
