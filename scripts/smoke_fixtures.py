"""Synthetic fixture provenance; independent of a runner surviving its upload.

An authenticated owner's listing plus a signed filename proves which fixtures a
release runner created, including an interruption before the local manifest write.
The signing key stays in the protected release environment.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
from pathlib import Path
from typing import Any


def signing_key() -> bytes:
    key = os.environ.get("PROD_SMOKE_FIXTURE_KEY", "").encode()
    if len(key) < 32:
        raise AssertionError("Fixture provenance: PROD_SMOKE_FIXTURE_KEY must contain at least 32 bytes")
    return key


def signed_filename(user_id: str, run_id: str) -> str:
    if not user_id or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise AssertionError("Fixture provenance: authenticated owner and valid run ID required")
    signature = hmac.new(signing_key(), f"standalone\n{user_id}\n{run_id}".encode(), hashlib.sha256).hexdigest()
    return f"ssci_v1_{run_id}_{signature}.pdf"


def verified_filename(filename: str, user_id: str) -> bool:
    match = re.fullmatch(r"ssci_v1_([0-9a-f]{32})_([0-9a-f]{64})\.pdf", filename)
    return bool(match and user_id and hmac.compare_digest(filename, signed_filename(user_id, match[1])))


def signed_conversation_title(user_id: str, run_id: str) -> str:
    if not user_id or not re.fullmatch(r"[0-9a-f]{32}", run_id):
        raise AssertionError("Fixture provenance: authenticated owner and valid run ID required")
    signature = hmac.new(signing_key(), f"native_conversation\n{user_id}\n{run_id}".encode(), hashlib.sha256).hexdigest()
    return f"ssci_thread_v1_{run_id}_{signature}"


def verified_conversation_title(title: str, user_id: str) -> bool:
    match = re.fullmatch(r"ssci_thread_v1_([0-9a-f]{32})_([0-9a-f]{64})", title)
    return bool(match and user_id and hmac.compare_digest(title, signed_conversation_title(user_id, match[1])))


class FixtureManager:
    """Atomic local receipts. Corruption or write failure must fail the gate."""

    def __init__(self, manifest_path: Path, run_id: str) -> None:
        self.manifest_path = manifest_path
        self.run_id = run_id

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"manifest_version": 1, "runs": {}}
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if data.get("manifest_version") != 1 or not isinstance(data.get("runs"), dict):
                raise ValueError("invalid manifest")
            return data
        except (OSError, ValueError, AttributeError) as exc:
            raise AssertionError("Fixture provenance: manifest cannot be read or validated") from exc

    def save_manifest(self, data: dict[str, Any]) -> None:
        temporary = self.manifest_path.with_suffix(".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        except OSError as exc:
            raise AssertionError("Fixture provenance: manifest could not be persisted") from exc

    def register_fixture(self, doc_id: str, filename: str, marker: str, user_id: str = "", email: str = "") -> None:
        del email  # Credentials and account email are never written to artifacts.
        data = self.load_manifest()
        entry = data["runs"].setdefault(self.run_id, {
            "run_id": self.run_id, "user_id": user_id, "created_at": time.time(), "documents": [],
        })
        entry["documents"].append({
            "doc_id": doc_id, "filename": filename, "marker": marker,
            "created_at": time.time(), "status": "pending_cleanup",
        })
        self.save_manifest(data)

    def register_conversation(self, conversation_id: str, title: str, user_id: str) -> None:
        data = self.load_manifest()
        entry = data["runs"].setdefault(self.run_id, {
            "run_id": self.run_id, "user_id": user_id, "created_at": time.time(), "documents": [],
        })
        entry.setdefault("conversations", []).append({
            "conversation_id": conversation_id, "title": title, "created_at": time.time(), "status": "pending_cleanup",
        })
        self.save_manifest(data)

    def _mark(self, doc_id: str, *, kind: str = "documents", **values: Any) -> None:
        data = self.load_manifest()
        for entry in data["runs"].values():
            for doc in entry.get(kind, []):
                if doc.get("doc_id" if kind == "documents" else "conversation_id") == doc_id:
                    doc.update(values)
        self.save_manifest(data)

    def mark_fixture_cleaned(self, doc_id: str) -> None:
        self._mark(doc_id, status="cleaned_up", cleaned_at=time.time())

    def mark_fixture_failed(self, doc_id: str, reason: str = "") -> None:
        self._mark(doc_id, status="failed_cleanup", failure_reason=reason)

    def mark_conversation_cleaned(self, conversation_id: str) -> None:
        self._mark(conversation_id, kind="conversations", status="cleaned_up", cleaned_at=time.time())

    def get_uncleaned_fixtures(self) -> list[dict[str, Any]]:
        return [
            {**doc, "user_id": entry.get("user_id")}
            for entry in self.load_manifest()["runs"].values()
            for doc in entry.get("documents", []) if doc.get("status") != "cleaned_up"
        ]
