"""Offline integrity and durable-write checks; no production credentials or HTTP."""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smoke_fixtures import FixtureManager, signed_filename


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("PROD_SMOKE_FIXTURE_KEY", "synthetic-native-receipt-signing-key-only")
    return FixtureManager(tmp_path / "receipt.json", "a" * 32)


def register(manager):
    manager.register_fixture("synthetic-document", signed_filename("synthetic-owner", "a" * 32),
        "SYNTHETIC-MARKER", user_id="synthetic-owner", email="never-persist@example.com")


def test_fresh_worker_reads_private_signed_pending_and_complete_receipts(manager):
    register(manager)
    assert manager.manifest_path.stat().st_mode & 0o077 == 0
    assert "never-persist" not in manager.manifest_path.read_text()
    restarted = FixtureManager(manager.manifest_path, "b" * 32)
    assert len(restarted.get_uncleaned_fixtures()) == 1
    restarted.mark_fixture_cleaned("synthetic-document")
    assert manager.get_uncleaned_fixtures() == []
    assert manager.load_manifest()["manifest_version"] == 2


@pytest.mark.parametrize("field,value", [
    ("doc_id", "different-document"), ("status", "cleaned_up"),
    ("filename", "different.pdf"), ("marker", "changed-marker"),
])
def test_tampered_id_state_or_provenance_is_rejected_before_cleanup(manager, field, value):
    register(manager)
    data = json.loads(manager.manifest_path.read_text())
    data["runs"]["a" * 32]["documents"][0][field] = value
    manager.manifest_path.write_text(json.dumps(data))
    with pytest.raises(AssertionError, match="cannot be read or validated"):
        manager.get_uncleaned_fixtures()


def test_key_change_and_unsigned_legacy_file_require_reconciliation(manager, monkeypatch):
    register(manager)
    monkeypatch.setenv("PROD_SMOKE_FIXTURE_KEY", "different-synthetic-key-with-enough-bytes")
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()
    manager.manifest_path.write_text('{"manifest_version":1,"runs":{}}')
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()


def test_symlink_read_write_and_fifo_are_refused_without_touching_targets(manager, tmp_path):
    target = tmp_path / "target"
    target.write_text("preserve unrelated content")
    manager.manifest_path.symlink_to(target)
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()
    with pytest.raises(AssertionError, match="persisted"):
        manager.save_manifest({"manifest_version": 2, "runs": {}})
    assert target.read_text() == "preserve unrelated content"
    manager.manifest_path.unlink()
    os.mkfifo(manager.manifest_path, 0o600)
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()


def test_nonprivate_and_oversized_manifests_are_rejected(manager):
    register(manager)
    manager.manifest_path.chmod(0o644)
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()
    manager.manifest_path.chmod(0o600)
    manager.manifest_path.write_bytes(b" " * (manager.MAX_BYTES + 1))
    with pytest.raises(AssertionError, match="manifest"):
        manager.load_manifest()


def test_capacity_and_failed_atomic_replacement_preserve_pending_receipts(manager, monkeypatch):
    register(manager)
    original = manager.manifest_path.read_bytes()
    data = manager.load_manifest()
    data["runs"]["a" * 32]["documents"] *= manager.MAX_ENTRIES + 1
    with pytest.raises(AssertionError, match="persisted"):
        manager.save_manifest(data)
    assert manager.manifest_path.read_bytes() == original

    def unavailable(*_):
        raise OSError("synthetic storage failure")
    monkeypatch.setattr(os, "replace", unavailable)
    with pytest.raises(AssertionError, match="persisted"):
        manager.mark_fixture_cleaned("synthetic-document")
    assert manager.manifest_path.read_bytes() == original
    assert len(manager.get_uncleaned_fixtures()) == 1
    assert sorted(path.name for path in manager.manifest_path.parent.iterdir()) == ["receipt.json"]


def test_missing_key_cannot_create_even_an_empty_success_receipt(manager, monkeypatch):
    monkeypatch.delenv("PROD_SMOKE_FIXTURE_KEY")
    with pytest.raises(AssertionError, match="key|KEY"):
        manager.save_manifest({"manifest_version": 2, "runs": {}})
    assert not manager.manifest_path.exists()
