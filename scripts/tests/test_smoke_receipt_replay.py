"""A restarted smoke runner must finish deletion even when catalogs are empty."""
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import ci_production_smoke as smoke

OWNER = "synthetic-owner"
DOCUMENT = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-smoke-replay-document"))
CONVERSATION = str(uuid.uuid5(uuid.NAMESPACE_URL, "synthetic-smoke-replay-conversation"))


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_FIXTURE_KEY", "synthetic-native-replay-private-key-only")
    value = smoke.FixtureManager(tmp_path / "receipts.json", "a" * 32)
    monkeypatch.setattr(smoke, "fixture_manager", value)
    smoke.SYNTHETIC_DOCUMENT_IDS.clear()
    smoke.SYNTHETIC_CONVERSATION_IDS.clear()
    monkeypatch.setattr(smoke.time, "sleep", lambda *_: None)
    return value


def document(manager, owner=OWNER):
    manager.register_fixture(DOCUMENT, smoke.signed_filename(owner, "a" * 32), "", user_id=owner)


def peer(calls, receipt_status=200, source_status=404, source=None):
    def request(url, **kwargs):
        method = kwargs.get("method", "GET")
        calls.append((method, url))
        if method == "DELETE":
            return 204, {}, {}
        if url.endswith("/cleanup"):
            return receipt_status, {}, {"document_id": DOCUMENT, "status": "complete",
                "vectors_remaining": 0, "derived_objects_remaining": 0, "object_deleted": True}
        return source_status, {}, source
    return request


def test_restarted_runner_rechecks_pending_tombstones_without_live_catalog_rows(manager, monkeypatch):
    document(manager)
    manager.register_conversation(CONVERSATION, smoke.signed_conversation_title(OWNER, "a" * 32), OWNER)
    monkeypatch.setattr(smoke, "fixture_manager", smoke.FixtureManager(manager.manifest_path, "b" * 32))
    calls = []
    monkeypatch.setattr(smoke, "http", peer(calls))
    smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    assert manager.get_uncleaned_fixtures() == manager.get_uncleaned_conversations() == []
    assert ("GET", smoke.API + "/documents/" + DOCUMENT + "/cleanup") in calls
    assert sum(method == "DELETE" for method, _ in calls) == 2


def test_receipt_503_preserves_signed_pending_identity_until_another_process_verifies_it(manager, monkeypatch):
    document(manager)
    monkeypatch.setattr(smoke, "http", peer([], receipt_status=503))
    with pytest.raises(AssertionError, match="Physical deletion verification"):
        smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    restarted = smoke.FixtureManager(manager.manifest_path, "b" * 32)
    assert len(restarted.get_uncleaned_fixtures()) == 1
    monkeypatch.setattr(smoke, "fixture_manager", restarted)
    monkeypatch.setattr(smoke, "http", peer([]))
    smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    assert manager.get_uncleaned_fixtures() == []


@pytest.mark.parametrize("status,source", [(503, {}), (200, {"id": DOCUMENT, "user_id": "foreign"})])
def test_unavailable_or_changed_current_source_cannot_trigger_deletion(manager, monkeypatch, status, source):
    document(manager)
    calls = []
    monkeypatch.setattr(smoke, "http", peer(calls, source_status=status, source=source))
    with pytest.raises(AssertionError, match="Fixture provenance"):
        smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    assert all(method == "GET" for method, _ in calls)
    assert len(manager.get_uncleaned_fixtures()) == 1


def test_every_pending_owner_is_checked_before_any_http_mutation(manager, monkeypatch):
    document(manager)
    manager.register_conversation(CONVERSATION, smoke.signed_conversation_title("foreign", "a" * 32), OWNER)
    calls = []
    monkeypatch.setattr(smoke, "http", peer(calls))
    with pytest.raises(AssertionError, match="ownership"):
        smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    assert calls == []


def test_catalog_discovery_persists_before_delete_then_empty_catalog_recovery_works(manager, monkeypatch):
    filename = smoke.signed_filename(OWNER, "a" * 32)
    monkeypatch.setattr(smoke, "get_all_documents", lambda _: [{"id": DOCUMENT, "user_id": OWNER, "filename": filename}])
    monkeypatch.setattr(smoke, "http", peer([], receipt_status=503))
    with pytest.raises(AssertionError, match="Physical deletion verification"):
        smoke.sweep_dangling_synthetic_documents("synthetic-token", user_id=OWNER)
    assert len(manager.get_uncleaned_fixtures()) == 1
    monkeypatch.setattr(smoke, "get_all_documents", lambda _: [])
    monkeypatch.setattr(smoke, "fixture_manager", smoke.FixtureManager(manager.manifest_path, "b" * 32))
    monkeypatch.setattr(smoke, "http", peer([]))
    smoke.reconcile_recorded_fixtures("synthetic-token", OWNER)
    assert manager.get_uncleaned_fixtures() == []


def test_rediscovered_completed_source_requires_verification_and_conflicting_owner_is_denied(manager):
    document(manager)
    manager.mark_fixture_cleaned(DOCUMENT)
    document(manager)
    assert len(manager.get_uncleaned_fixtures()) == 1
    assert len(manager.load_manifest()["runs"]["a" * 32]["documents"]) == 1
    with pytest.raises(AssertionError, match="conflicting"):
        document(manager, "different-owner")
