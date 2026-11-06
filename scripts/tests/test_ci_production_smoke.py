"""Offline regression tests for the real-HTTP production smoke runner.

These never contact the production backend.
"""
import io
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
def clean_state(monkeypatch, tmp_path):
    smoke.SYNTHETIC_DOCUMENT_IDS.clear()
    monkeypatch.setenv("PROD_SMOKE_FIXTURE_KEY", "synthetic-signing-key-for-offline-tests-only")
    monkeypatch.setattr(smoke, "fixture_manager", smoke.FixtureManager(tmp_path / "manifest.json", "a" * 32))
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

    doc_id, was_created = smoke.ensure_synthetic_document("token_xyz", "MARKER-123", user_id="user-1")
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
        return 200, {}, cleanup_receipt("doc-synth-001")

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
                "answer": f"The verification code is {marker}. [1]",
                "sources": [valid_source(doc_id, marker)],
            }
        return 404, {}, {}

    fake_sse = [
        ("metadata", {"query": "test"}),
        ("source", {"sources": [valid_source(doc_id, marker)]}),
        ("token", {"token": f"Code is {marker}. [1]"}),
        ("done", {"answer": f"Code is {marker}. [1]"}),
    ]
    monkeypatch.setattr(smoke, "http", fake_http)
    monkeypatch.setattr(smoke, "read_sse_stream", lambda *args, **kwargs: fake_sse)

    smoke.run_real_rag_pipeline("token_abc", doc_id, marker, do_answer=True)
    assert any("Real semantic search" in r for r in smoke.RESULTS)
    assert any("Real grounded answer" in r for r in smoke.RESULTS)
    assert any("SSE answer streaming" in r for r in smoke.RESULTS)
    assert any("Cross-encoder reranking verification" in r for r in smoke.RESULTS)


def hosted_diagnostics():
    return {
        "database": "ok", "vector_store": "supabase", "local_model_weights": False,
        "inference": {
            "provider": "cohere", "mode": "hosted",
            "embedding_model": "embed-english-light-v3.0", "rerank_model": "rerank-v3.5",
        },
    }


def test_check_diagnostics_requires_hosted_cohere_and_postgres(monkeypatch):
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, hosted_diagnostics()))
    smoke.check_diagnostics()
    assert any("Diagnostic probe" in r and "hosted Cohere" in r for r in smoke.RESULTS)


@pytest.mark.parametrize("backend", ["qdrant", "embedded", "unknown"])
def test_check_diagnostics_rejects_other_vector_backends(monkeypatch, backend):
    payload = hosted_diagnostics()
    payload["vector_store"] = backend
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, payload))
    with pytest.raises(AssertionError, match="PostgreSQL/pgvector"):
        smoke.check_diagnostics()


@pytest.mark.parametrize("field,value", [("provider", "other"), ("mode", "local"), ("embedding_model", "different"), ("rerank_model", "unknown")])
def test_check_diagnostics_rejects_unexpected_inference_configuration(monkeypatch, field, value):
    payload = hosted_diagnostics()
    payload["inference"][field] = value
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, payload))
    with pytest.raises(AssertionError, match="Hosted-only"):
        smoke.check_diagnostics()



def test_ensure_synthetic_document_rejects_zero_or_missing_chunk_count(monkeypatch):
    monkeypatch.setattr(smoke.time, "sleep", lambda *_: None)
    monkeypatch.setattr(smoke, "http_upload", lambda *args, **kwargs: (201, {}, {"id": "doc-001"}))

    # Test chunk_count = 0
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"status": "indexed", "chunk_count": 0}))
    with pytest.raises(AssertionError, match="chunk_count=0"):
        smoke.ensure_synthetic_document("token_xyz", "MARKER", user_id="user-1")

    # Test missing chunk_count
    monkeypatch.setattr(smoke, "http", lambda *args, **kwargs: (200, {}, {"status": "indexed"}))
    with pytest.raises(AssertionError, match="chunk_count is missing"):
        smoke.ensure_synthetic_document("token_xyz", "MARKER", user_id="user-1")


def test_cleanup_synthetic_document_checks_vector_purge(monkeypatch):
    def fake_http(url, **kwargs):
        if kwargs.get("method") == "DELETE":
            return 204, {}, {}
        if url.endswith("/search"):
            return 200, {}, {"results": []}
        return 200, {}, cleanup_receipt("doc-001")

    monkeypatch.setattr(smoke, "http", fake_http)
    smoke.cleanup_synthetic_document("token_xyz", "doc-001", marker="MARKER-123")
    assert any("purged synthetic document" in r for r in smoke.RESULTS)
    assert any("Vector deletion verification" in r for r in smoke.RESULTS)


def cleanup_receipt(document_id):
    return {"document_id": document_id, "status": "complete", "vectors_remaining": 0, "object_deleted": True}


def valid_source(document_id, marker):
    return {"document_id": document_id, "source_id": 1, "start_page": 1, "end_page": 1, "content": marker}


def test_offset_pagination_reads_second_page(monkeypatch):
    calls = []
    def fake_http(url, **kwargs):
        calls.append(url)
        offset = 0 if "offset=0&" in url else 50
        docs = [{"id": str(i)} for i in range(offset, min(offset + 50, 51))]
        return 200, {}, {"documents": docs, "total": 51}
    monkeypatch.setattr(smoke, "http", fake_http)
    assert len(smoke.get_all_documents("token")) == 51
    assert "offset=50&limit=50" in calls[1]
    assert all("page=" not in url for url in calls)


@pytest.mark.parametrize("status,payload", [(503, {}), (200, {}), (200, {"documents": [], "total": 2})])
def test_listing_fails_on_errors_or_incomplete_page(monkeypatch, status, payload):
    monkeypatch.setattr(smoke, "http", lambda *a, **kw: (status, {}, payload))
    with pytest.raises(AssertionError, match="Fixture listing"):
        smoke.get_all_documents("token")


def test_listing_fails_if_api_ignores_offset(monkeypatch):
    monkeypatch.setattr(smoke, "http", lambda *a, **kw: (200, {}, {
        "documents": [{"id": str(i)} for i in range(50)], "total": 51,
    }))
    with pytest.raises(AssertionError, match="repeated"):
        smoke.get_all_documents("token")


@pytest.mark.parametrize("status,payload", [(503, {}), (200, {"results": [{"document_id": "doc-001"}]})])
def test_deletion_search_outage_or_remaining_results_fail(monkeypatch, status, payload):
    smoke.SYNTHETIC_DOCUMENT_IDS.add("doc-001")
    monkeypatch.setattr(smoke, "http", lambda *a, **kw: (204, {}, {}) if kw.get("method") == "DELETE" else (status, {}, payload))
    with pytest.raises(AssertionError, match="Deletion access verification"):
        smoke.cleanup_synthetic_document("token", "doc-001", marker="MARKER")
    assert "doc-001" in smoke.SYNTHETIC_DOCUMENT_IDS
    assert not any("purged synthetic document" in r for r in smoke.RESULTS)


@pytest.mark.parametrize("receipt", [None, {}, {"document_id": "doc-001", "status": "complete", "vectors_remaining": 1, "object_deleted": True},
                                     {"document_id": "doc-001", "status": "complete", "vectors_remaining": 0, "object_deleted": False},
                                     {"document_id": "another-document", "status": "complete", "vectors_remaining": 0, "object_deleted": True}])
def test_physical_cleanup_cannot_pass_without_scoped_receipt(monkeypatch, receipt):
    monkeypatch.setattr(smoke, "http", lambda *a, **kw: (204, {}, {}) if kw.get("method") == "DELETE" else (200, {}, receipt))
    with pytest.raises(AssertionError, match="Physical deletion verification"):
        smoke.cleanup_synthetic_document("token", "doc-001")


def test_sweep_accepts_signed_same_owner_fixture_after_lost_manifest(monkeypatch):
    from smoke_fixtures import signed_filename
    docs = [
        {"id": "trusted", "user_id": "owner", "filename": signed_filename("owner", "a" * 32)},
        {"id": "wrong-owner", "user_id": "other", "filename": signed_filename("owner", "b" * 32)},
        {"id": "name-only", "user_id": "owner", "filename": "ci_smoke_test.pdf"},
        {"id": "forged", "user_id": "owner", "filename": signed_filename("other", "c" * 32)},
    ]
    deleted = []
    monkeypatch.setattr(smoke, "get_all_documents", lambda *_: docs)
    monkeypatch.setattr(smoke, "cleanup_synthetic_document", lambda token, doc_id: deleted.append(doc_id))
    smoke.sweep_dangling_synthetic_documents("token", user_id="owner")
    assert deleted == ["trusted"]


def test_corrupt_manifest_fails_instead_of_resetting(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text("invalid")
    with pytest.raises(AssertionError, match="manifest"):
        smoke.FixtureManager(path, "run").load_manifest()


def test_missing_credentials_fail_required_journey(monkeypatch):
    monkeypatch.setattr(smoke, "acquire_ci_auth", lambda: None)
    with pytest.raises(AssertionError, match="credentials are required"):
        smoke.run_rag_suite()


@pytest.mark.parametrize("answer,sources", [
    ("Unrelated answer [1]", [{"document_id": "doc", "source_id": 1, "start_page": 1, "end_page": 1, "content": "MARKER"}]),
    ("MARKER [99]", [{"document_id": "doc", "source_id": 1, "start_page": 1, "end_page": 1, "content": "MARKER"}]),
    ("MARKER [1]", [{"document_id": "other", "source_id": 1, "start_page": 1, "end_page": 1, "content": "MARKER"}]),
    ("MARKER [1]", [{"document_id": "doc", "source_id": 1, "start_page": 2, "end_page": 2, "content": "MARKER"}]),
    ("MARKER [1]", [{"document_id": "doc", "source_id": 1, "start_page": 1, "end_page": 1, "content": "unrelated"}]),
])
def test_grounding_assertions_reject_wrong_answer_or_citations(answer, sources):
    with pytest.raises(AssertionError):
        smoke.verify_grounded_marker(answer, sources, "doc", "MARKER", "grounding")


@pytest.mark.parametrize("events", [
    [("metadata", {}), ("token", {"token": "MARKER [1]"})],
    [("metadata", {}), ("error", {"code": "provider_unavailable"}), ("done", {"answer": "MARKER [1]"})],
    [("metadata", {}), ("done", {"answer": "MARKER [1]"}), ("done", {"answer": "MARKER [1]"})],
    [("metadata", {}), ("source", {"sources": [{"document_id": "doc", "source_id": 1, "start_page": 1, "end_page": 1, "content": "MARKER"}]}),
     ("token", {"token": "different answer"}), ("done", {"answer": "MARKER [1]"})],
])
def test_sse_requires_successful_matching_completion(monkeypatch, events):
    def fake_http(url, **kwargs):
        if url.endswith('/search'):
            return 200, {}, {"results": [{"document_id": "doc", "content": "MARKER", "rerank_score": 0.8}]}
        return 200, {}, {"answer": "MARKER [1]", "sources": [valid_source("doc", "MARKER")]}
    monkeypatch.setattr(smoke, 'http', fake_http)
    monkeypatch.setattr(smoke, 'read_sse_stream', lambda *a, **kw: events)
    with pytest.raises(AssertionError, match="SSE answer streaming"):
        smoke.run_real_rag_pipeline("token", "doc", "MARKER", do_answer=True)
