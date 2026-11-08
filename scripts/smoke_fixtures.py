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
import stat
import tempfile
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
    """Private atomic receipts; the signature binds IDs as well as fixture names."""

    MAX_BYTES = 262144
    MAX_ENTRIES = 128

    def __init__(self, manifest_path: Path, run_id: str) -> None:
        self.manifest_path = manifest_path
        self.run_id = run_id

    def load_manifest(self) -> dict[str, Any]:
        try:
            fd = os.open(self.manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return {"manifest_version": 2, "runs": {}}
        except OSError:
            raise AssertionError("Fixture provenance: manifest cannot be read or validated") from None
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.geteuid() or info.st_size > self.MAX_BYTES):
                    raise ValueError("unsafe manifest")
                data = json.loads(handle.read(self.MAX_BYTES + 1))
            self._validate(data)
            signature = data.pop("signature", None)
            if not isinstance(signature, str) or not hmac.compare_digest(signature, self._signature(data)):
                raise ValueError("invalid manifest signature")
            return data
        except (OSError, ValueError, TypeError, AttributeError):
            raise AssertionError("Fixture provenance: manifest cannot be read or validated") from None

    @staticmethod
    def _signature(data: dict[str, Any]) -> str:
        body = json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        return hmac.new(signing_key(), b"standalone-receipts-v2\n" + body, hashlib.sha256).hexdigest()

    def _validate(self, data: dict[str, Any]) -> None:
        if not isinstance(data, dict) or data.get("manifest_version") != 2 or not isinstance(data.get("runs"), dict):
            raise ValueError("invalid manifest")
        count = 0
        for entry in data["runs"].values():
            if not isinstance(entry, dict):
                raise TypeError("invalid run")
            for kind in ("documents", "conversations"):
                rows = entry.get(kind, [])
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise ValueError("invalid entries")
                count += len(rows)
        if len(data["runs"]) > self.MAX_ENTRIES or count > self.MAX_ENTRIES:
            raise ValueError("manifest capacity requires reconciliation")

    def save_manifest(self, data: dict[str, Any]) -> None:
        temporary = None
        try:
            self._validate(data)
            # Re-sign a copy so callers cannot reuse a stale signature after mutation.
            body = {key: value for key, value in data.items() if key != "signature"}
            payload = json.dumps({**body, "signature": self._signature(body)}, allow_nan=False).encode()
            if len(payload) > self.MAX_BYTES:
                raise ValueError("manifest too large")
            if self.manifest_path.is_symlink():
                raise ValueError("unsafe manifest target")
            fd, name = tempfile.mkstemp(prefix=self.manifest_path.name + ".", dir=self.manifest_path.parent)
            temporary = Path(name)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload.decode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.manifest_path)
        except (OSError, ValueError, TypeError):
            raise AssertionError("Fixture provenance: manifest could not be persisted") from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def register_fixture(self, doc_id: str, filename: str, marker: str, user_id: str = "", email: str = "") -> None:
        del email  # Credentials and account email are never written to artifacts.
        data = self.load_manifest()
        for prior in data["runs"].values():
            for row in prior.get("documents", []):
                if row.get("doc_id") == doc_id:
                    if prior.get("user_id") != user_id or row.get("filename") != filename:
                        raise AssertionError("Fixture provenance: conflicting document receipt")
                    row["status"] = "pending_cleanup"
                    self.save_manifest(data)
                    return
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
        for prior in data["runs"].values():
            for row in prior.get("conversations", []):
                if row.get("conversation_id") == conversation_id:
                    if prior.get("user_id") != user_id or row.get("title") != title:
                        raise AssertionError("Fixture provenance: conflicting conversation receipt")
                    row["status"] = "pending_cleanup"
                    self.save_manifest(data)
                    return
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

    def get_uncleaned_conversations(self) -> list[dict[str, Any]]:
        return [
            {**row, "user_id": entry.get("user_id")}
            for entry in self.load_manifest()["runs"].values()
            for row in entry.get("conversations", []) if row.get("status") != "cleaned_up"
        ]
