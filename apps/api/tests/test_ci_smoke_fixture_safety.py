import sys
from pathlib import Path

import pytest

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.ci_production_smoke as smoke  # noqa: E402


@pytest.fixture
def tmp_manifest(tmp_path, monkeypatch):
    manifest_file = tmp_path / "ci_smoke_manifest.json"
    monkeypatch.setattr(smoke, "MANIFEST_PATH", manifest_file)
    return manifest_file


def test_fixture_manifest_lifecycle(tmp_manifest):
    """Verify that fixtures can be registered, tracked, and updated in persistent manifest."""
    fm = smoke.FixtureManager(manifest_path=tmp_manifest, run_id="test_run_123")
    assert tmp_manifest.exists()

    # Register fixture
    fm.register_fixture(
        doc_id="doc-uuid-001",
        filename="ci_smoke_test_run_123.pdf",
        marker="MARKER-123",
        user_id="user-test-999",
        email="ci_test@example.com",
    )

    data = fm.load_manifest()
    assert "test_run_123" in data["runs"]
    run_entry = data["runs"]["test_run_123"]
    assert len(run_entry["documents"]) == 1
    assert run_entry["documents"][0]["doc_id"] == "doc-uuid-001"
    assert run_entry["documents"][0]["status"] == "pending_cleanup"

    # Mark cleaned
    fm.mark_fixture_cleaned("doc-uuid-001")
    data = fm.load_manifest()
    assert data["runs"]["test_run_123"]["documents"][0]["status"] == "cleaned_up"


def test_snapshot_iteration_cleans_all_fixtures(tmp_manifest, monkeypatch):
    """Verify that deleting multiple fixtures does not skip elements due to list mutation."""
    fm = smoke.FixtureManager(manifest_path=tmp_manifest, run_id="test_run_multi")
    monkeypatch.setattr(smoke, "fixture_manager", fm)

    # Register 4 fixtures in SYNTHETIC_DOCUMENT_IDS
    fixtures = ["doc-1", "doc-2", "doc-3", "doc-4"]
    smoke.SYNTHETIC_DOCUMENT_IDS.clear()
    for f_id in fixtures:
        fm.register_fixture(
            doc_id=f_id,
            filename=f"ci_smoke_{f_id}.pdf",
            marker=f"MARKER-{f_id}",
            user_id="user-ci-1",
            email="ci@example.com",
        )
        smoke.SYNTHETIC_DOCUMENT_IDS.add(f_id)

    deleted_ids = []

    def mock_http(url, method="GET", headers=None, data=None, timeout=15):
        if method == "DELETE":
            doc_id = url.split("/")[-1]
            deleted_ids.append(doc_id)
            return 200, {}, {"status": "ok"}
        if method == "POST" and "/search" in url:
            return 200, {}, {"results": []}
        return 200, {}, {}

    monkeypatch.setattr(smoke, "http", mock_http)

    # Clean up using snapshot iteration
    for pending_id in list(smoke.SYNTHETIC_DOCUMENT_IDS):
        smoke.cleanup_synthetic_document("mock_token", pending_id)

    assert set(deleted_ids) == set(fixtures)
    assert len(deleted_ids) == 4
    assert len(smoke.SYNTHETIC_DOCUMENT_IDS) == 0


def test_provenance_and_ownership_protects_user_documents(tmp_manifest, monkeypatch):
    """Ensure user documents belonging to other users or missing provenance are NEVER deleted."""
    fm = smoke.FixtureManager(manifest_path=tmp_manifest, run_id="test_run_sweep")
    monkeypatch.setattr(smoke, "fixture_manager", fm)

    active_ci_user_id = "ci-user-uuid-111"
    active_ci_email = "ci_tester@example.com"

    # Mock multi-page document list
    mock_docs_page_1 = [
        # 1. Genuine user doc under real account
        {
            "id": "real-user-doc-1",
            "filename": "HarshMishra_Resume.pdf",
            "user_id": "real-user-a353db5c",
            "created_at": "2026-09-24T15:19:20Z",
        },
        # 2. Another user's test file - MUST NOT BE TOUCHED
        {
            "id": "other-user-ci-doc-2",
            "filename": "ci_smoke_test.pdf",
            "user_id": "different-user-9999",
            "created_at": "2026-10-01T20:00:00Z",
        },
        # 3. Genuine user file with 'test' in name - MUST NOT BE TOUCHED
        {
            "id": "real-user-test-report",
            "filename": "test_blood_work.pdf",
            "user_id": "real-user-a353db5c",
            "created_at": "2026-11-01T12:00:00Z",
        },
        # 4. Old orphaned CI fixture belonging to active CI test user
        {
            "id": "ci-orphaned-doc-4",
            "filename": "ci_smoke_old_run.pdf",
            "user_id": active_ci_user_id,
            "created_at": "2026-11-04T10:00:00Z",
        },
    ]

    deleted_calls = []

    def mock_http(url, method="GET", headers=None, data=None, timeout=15):
        if method == "GET" and "/documents" in url:
            if "page=1" in url or "page" not in url:
                return 200, {}, {"documents": mock_docs_page_1}
            return 200, {}, {"documents": []}
        if method == "DELETE":
            doc_id = url.split("/")[-1]
            deleted_calls.append(doc_id)
            return 204, {}, {}
        return 200, {}, {}

    monkeypatch.setattr(smoke, "http", mock_http)

    # Perform sweep with verified account ownership
    smoke.sweep_dangling_synthetic_documents(
        token="test-token",
        user_id=active_ci_user_id,
        user_email=active_ci_email,
    )

    # ONLY doc 4 (matching active_ci_user_id and synthetic pattern) may be swept!
    assert deleted_calls == ["ci-orphaned-doc-4"]
    assert "real-user-doc-1" not in deleted_calls
    assert "other-user-ci-doc-2" not in deleted_calls
    assert "real-user-test-report" not in deleted_calls


def test_multi_page_pagination(tmp_manifest, monkeypatch):
    """Verify that sweep fetches across multiple pages to find all orphaned fixtures."""
    active_ci_user_id = "ci-user-123"

    page_1 = [{"id": f"page1-doc-{i}", "filename": f"other_{i}.pdf", "user_id": active_ci_user_id} for i in range(50)]
    page_2 = [{"id": "ci-dangling-page2", "filename": "ci_smoke_run99.pdf", "user_id": active_ci_user_id}]

    requested_pages = []

    def mock_http(url, method="GET", headers=None, data=None, timeout=15):
        if method == "GET" and "/documents" in url:
            import urllib.parse
            parsed = urllib.parse.urlparse(url)
            params = urllib.parse.parse_qs(parsed.query)
            page = int(params.get("page", ["1"])[0])
            requested_pages.append(page)
            if page == 1:
                return 200, {}, {"documents": page_1}
            elif page == 2:
                return 200, {}, {"documents": page_2}
            return 200, {}, {"documents": []}
        if method == "DELETE":
            return 200, {}, {}
        return 200, {}, {}

    monkeypatch.setattr(smoke, "http", mock_http)

    smoke.sweep_dangling_synthetic_documents(
        token="test-token",
        user_id=active_ci_user_id,
        user_email="ci@test.com",
    )

    assert 1 in requested_pages
    assert 2 in requested_pages


def test_cleanup_retries_on_transient_error(tmp_manifest, monkeypatch):
    """Verify that cleanup retries on HTTP 500 or 429 before succeeding."""
    fm = smoke.FixtureManager(manifest_path=tmp_manifest, run_id="test_run_retry")
    monkeypatch.setattr(smoke, "fixture_manager", fm)

    attempts = 0

    def mock_http(url, method="GET", headers=None, data=None, timeout=15):
        nonlocal attempts
        if method == "DELETE":
            attempts += 1
            if attempts < 3:
                return 500, {}, {"detail": "temporary error"}
            return 200, {}, {"status": "ok"}
        if method == "POST" and "/search" in url:
            return 200, {}, {"results": []}
        return 200, {}, {}

    monkeypatch.setattr(smoke, "http", mock_http)

    success = smoke.cleanup_synthetic_document("token", "doc-retry-123", max_retries=3)
    assert success is True
    assert attempts == 3
