"""Offline transport/archive boundaries; synthetic metadata only."""
import copy
import io
import stat
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_artifacts as artifacts


def run(identity=123):
    return {"id": identity, "head_sha": "a" * 40, "head_branch": "main", "event": "workflow_dispatch",
        "path": ".github/workflows/release.yml", "repository": {"full_name": artifacts.REPOSITORY},
        "head_repository": {"full_name": artifacts.REPOSITORY}}


def artifact():
    return {"id": 456, "name": "receipts", "expired": False, "size_in_bytes": 100,
        "workflow_run": {"id": 123, "head_sha": "a" * 40, "head_branch": "main"}}


def zip_payload(name="baseline.json", content=b'{"synthetic":true}', mode=stat.S_IFREG | 0o600):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entry = zipfile.ZipInfo(name)
        entry.external_attr = mode << 16
        archive.writestr(entry, content)
    return output.getvalue()


def test_complete_bounded_history_uses_the_trusted_repository_and_all_pages(monkeypatch):
    calls = []
    def github(path):
        calls.append(path)
        rows = [run(i) for i in range(1, 101)] if path.endswith("&page=1") else [run(101)]
        return {"total_count": 101, "workflow_runs": rows}
    monkeypatch.setattr(artifacts, "github", github)
    assert len(artifacts.workflow_runs("release.yml")) == 101
    assert len(calls) == 2 and all("branch=main&event=workflow_dispatch" in path for path in calls)


@pytest.mark.parametrize("field,value", [("id", True), ("id", -1), ("path", ".github/workflows/foreign.yml"),
    ("event", "pull_request"), ("head_branch", "untrusted"), ("repository", None),
    ("head_repository", {"full_name": "foreign/repository"})])
def test_foreign_or_incomplete_history_is_rejected(monkeypatch, field, value):
    row = {**run(), field: value}
    monkeypatch.setattr(artifacts, "github", lambda path: {"total_count": 1, "workflow_runs": [row]})
    with pytest.raises(artifacts.ReleaseError): artifacts.workflow_runs("release.yml")


@pytest.mark.parametrize("count,rows", [(1000, []), (True, []), (2, [run()]), (2, [run(), run()])])
def test_history_bounds_missing_pages_and_duplicate_ids_fail_closed(monkeypatch, count, rows):
    monkeypatch.setattr(artifacts, "github", lambda path: {"total_count": count, "workflow_runs": rows})
    with pytest.raises(artifacts.ReleaseError): artifacts.workflow_runs("release.yml")


def test_changing_paginated_history_and_unknown_workflows_fail_closed(monkeypatch):
    monkeypatch.setattr(artifacts, "github", lambda path:
        {"total_count": 101, "workflow_runs": [run(i) for i in range(1, 101)]} if path.endswith("&page=1") else
        {"total_count": 102, "workflow_runs": [run(101), run(102)]})
    with pytest.raises(artifacts.ReleaseError): artifacts.workflow_runs("release.yml")
    with pytest.raises(artifacts.ReleaseError): artifacts.workflow_runs("../foreign")


def test_artifact_selection_binds_exact_name_run_revision_and_branch(monkeypatch):
    value = artifact()
    monkeypatch.setattr(artifacts, "github", lambda path: {"total_count": 1, "artifacts": [value]})
    assert artifacts.artifact_for(run(), "receipts") == value
    assert artifacts.artifact_for(run(), "missing") is None
    assert value == artifact()


@pytest.mark.parametrize("field,value", [("id", True), ("expired", True), ("size_in_bytes", 0),
    ("size_in_bytes", artifacts.MAX_ARCHIVE + 1), ("workflow_run", {"id": 999}),
    ("workflow_run", {"id": 123, "head_sha": "b" * 40, "head_branch": "main"})])
def test_unusable_artifacts_are_rejected(monkeypatch, field, value):
    row = {**artifact(), field: value}
    monkeypatch.setattr(artifacts, "github", lambda path: {"total_count": 1, "artifacts": [row]})
    with pytest.raises(artifacts.ReleaseError): artifacts.artifact_for(run(), "receipts")


def test_ambiguous_or_incomplete_artifact_lists_are_rejected(monkeypatch):
    for rows, count in [([artifact(), copy.deepcopy(artifact())], 2), ([], 1), ([], 100)]:
        monkeypatch.setattr(artifacts, "github", lambda path, count=count, rows=rows: {"total_count": count, "artifacts": rows})
        with pytest.raises(artifacts.ReleaseError): artifacts.artifact_for(run(), "receipts")


def test_valid_archive_is_read_in_memory():
    assert artifacts.archive_json(zip_payload(), "baseline.json") == {"synthetic": True}


@pytest.mark.parametrize("name,content,mode", [("../baseline.json", b"{}", stat.S_IFREG),
    ("baseline.json", b"target", stat.S_IFLNK), ("baseline.json", b"{}", stat.S_IFIFO),
    ("baseline.json/", b"{}", stat.S_IFDIR), ("baseline.json", b"invalid JSON", stat.S_IFREG),
    ("baseline.json", b'{"same":1,"same":2}', stat.S_IFREG),
    ("baseline.json", b"x" * (artifacts.MAX_PAYLOAD + 1), stat.S_IFREG)])
def test_unsafe_archive_entries_or_payloads_fail_closed(name, content, mode):
    with pytest.raises(artifacts.ReleaseError):
        artifacts.archive_json(zip_payload(name, content, mode), "baseline.json")


def test_oversized_invalid_or_multiple_file_archives_are_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("baseline.json", "{}")
        archive.writestr("another.json", "{}")
    for payload in (output.getvalue(), b"not a zip", b"x" * (artifacts.MAX_ARCHIVE + 1)):
        with pytest.raises(artifacts.ReleaseError): artifacts.archive_json(payload, "baseline.json")


@pytest.mark.parametrize("payload,code,success", [(b"synthetic", 0, True), (b"synthetic", 1, False),
    (b"x" * (artifacts.MAX_ARCHIVE + 1), 0, False)])
def test_download_binds_integer_id_fixed_repository_and_byte_bound(monkeypatch, payload, code, success):
    calls, stopped = [], []
    class Process:
        stdout = io.BytesIO(payload)
        def wait(self, timeout=None): return code
        def kill(self): stopped.append(True)
    def start(command, **kwargs):
        calls.append(command)
        assert kwargs["stderr"] == artifacts.subprocess.DEVNULL
        return Process()
    monkeypatch.setattr(artifacts.subprocess, "Popen", start)
    if success: assert artifacts.archive_bytes(artifact()) == payload
    else:
        with pytest.raises(artifacts.ReleaseError): artifacts.archive_bytes(artifact())
    assert calls == [["gh", "api", f"repos/{artifacts.REPOSITORY}/actions/artifacts/456/zip"]]
    assert stopped
    with pytest.raises(artifacts.ReleaseError): artifacts.archive_bytes({"id": "../foreign"})
