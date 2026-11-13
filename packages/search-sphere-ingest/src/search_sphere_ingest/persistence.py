"""Optional immutable local result storage; hosted deployments inject persistent storage."""

import os
from pathlib import Path
from typing import Protocol

from .models import IngestResult, stable_id


class DirectoryAssetStore:
    """Private local crops. Use a separately authorized directory per tenant.

    Production hosts should inject their private persistent object-store adapter.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory.resolve()

    def put(self, asset_id: str, content: bytes, media_type: str) -> None:
        if (
            len(asset_id) != 64
            or any(char not in "0123456789abcdef" for char in asset_id)
            or media_type != "image/png"
            or not content.startswith(b"\x89PNG")
            or len(content) > 16 * 1024 * 1024
        ):
            raise ValueError("Invalid private image asset")
        path = self.directory / (asset_id + ".png")
        temporary = self.directory / (os.urandom(16).hex() + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise ValueError("Immutable asset content conflict") from None
        finally:
            temporary.unlink(missing_ok=True)


class ResultStore(Protocol):
    def save(self, namespace: str, result: IngestResult) -> str: ...

    def load(self, namespace: str, identity: str) -> IngestResult: ...


class DirectoryResultStore:
    """For local use only. Namespace is trusted caller authorization context.

    Never mount this directory as a public static site; never use Render's
    ephemeral filesystem as durable storage. Source originals remain caller-owned.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.directory = directory.resolve()

    def _path(self, namespace: str, identity: str) -> Path:
        if (
            not namespace
            or len(identity) != 64
            or any(char not in "0123456789abcdef" for char in identity)
        ):
            raise ValueError("Invalid namespace or result identity")
        parent = self.directory / stable_id(namespace)
        parent.mkdir(mode=0o700, exist_ok=True)
        return parent / (identity + ".json")

    def save(self, namespace: str, result: IngestResult) -> str:
        doc = result.document
        identity = stable_id(
            doc.source_sha256,
            doc.pipeline_version,
            doc.config_fingerprint,
            [chunk.tokenizer for chunk in result.chunks],
        )
        path = self._path(namespace, identity)
        data = result.model_dump_json()
        # Trace IDs/timings are nondeterministic. Existing successful versions are immutable.
        if path.exists():
            self.load(namespace, identity)
            return identity
        temporary = path.with_suffix("." + os.urandom(8).hex() + ".tmp")
        try:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        return identity

    def load(self, namespace: str, identity: str) -> IngestResult:
        path = self._path(namespace, identity)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Stored result exceeds limit")
        return IngestResult.model_validate_json(path.read_text())
