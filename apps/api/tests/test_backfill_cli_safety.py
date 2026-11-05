from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure repository root is on sys.path so scripts can be imported under CI where PYTHONPATH=apps/api
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.backfill_pgvector_chunks import (  # noqa: E402
    BackfillStats,
    build_parser,
    main,
)


def test_build_parser_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.execute is False
    assert args.dry_run is False
    assert args.limit is None
    assert args.max_documents is None
    assert args.document_id is None
    assert args.target == "all"


@pytest.mark.asyncio
async def test_execute_without_bounds_fails_safety_check(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.ERROR)
    exit_code = await main(argv=["--execute"])
    assert exit_code == 2
    assert "Safety error: Live execution (--execute) requires explicit bounded scope" in caplog.text


@pytest.mark.asyncio
async def test_dry_run_default_prevents_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    mock_audit = AsyncMock(
        return_value={
            "documents": BackfillStats(total_audited=1, reindexed_success=1),
            "external_documents": BackfillStats(total_audited=0),
        }
    )

    with (
        patch("scripts.backfill_pgvector_chunks.create_async_engine") as mock_engine,
        patch("scripts.backfill_pgvector_chunks.audit_and_backfill", mock_audit),
    ):
        mock_engine.return_value.dispose = AsyncMock()
        exit_code = await main(argv=["--limit", "5"])
        assert exit_code == 0
        mock_audit.assert_awaited_once()
        _, kwargs = mock_audit.call_args
        assert kwargs["dry_run"] is True
        assert kwargs["limit"] == 5


@pytest.mark.asyncio
async def test_execute_with_limit_allows_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    mock_audit = AsyncMock(
        return_value={
            "documents": BackfillStats(total_audited=2, reindexed_success=2),
            "external_documents": BackfillStats(total_audited=0),
        }
    )

    with (
        patch("scripts.backfill_pgvector_chunks.create_async_engine") as mock_engine,
        patch("scripts.backfill_pgvector_chunks.audit_and_backfill", mock_audit),
    ):
        mock_engine.return_value.dispose = AsyncMock()
        exit_code = await main(argv=["--execute", "--limit", "2"])
        assert exit_code == 0
        mock_audit.assert_awaited_once()
        _, kwargs = mock_audit.call_args
        assert kwargs["dry_run"] is False
        assert kwargs["limit"] == 2


@pytest.mark.asyncio
async def test_execute_with_max_documents_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    mock_audit = AsyncMock(
        return_value={
            "documents": BackfillStats(total_audited=3, reindexed_success=3),
            "external_documents": BackfillStats(total_audited=0),
        }
    )

    with (
        patch("scripts.backfill_pgvector_chunks.create_async_engine") as mock_engine,
        patch("scripts.backfill_pgvector_chunks.audit_and_backfill", mock_audit),
    ):
        mock_engine.return_value.dispose = AsyncMock()
        exit_code = await main(argv=["--execute", "--max-documents", "3"])
        assert exit_code == 0
        mock_audit.assert_awaited_once()
        _, kwargs = mock_audit.call_args
        assert kwargs["dry_run"] is False
        assert kwargs["limit"] == 3


@pytest.mark.asyncio
async def test_execute_with_document_id_allows_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    mock_audit = AsyncMock(
        return_value={
            "documents": BackfillStats(total_audited=1, reindexed_success=1),
            "external_documents": BackfillStats(total_audited=0),
        }
    )

    with (
        patch("scripts.backfill_pgvector_chunks.create_async_engine") as mock_engine,
        patch("scripts.backfill_pgvector_chunks.audit_and_backfill", mock_audit),
    ):
        mock_engine.return_value.dispose = AsyncMock()
        exit_code = await main(argv=["--execute", "--document-id", "doc-1234"])
        assert exit_code == 0
        mock_audit.assert_awaited_once()
        _, kwargs = mock_audit.call_args
        assert kwargs["dry_run"] is False
        assert kwargs["document_id"] == "doc-1234"


@pytest.mark.asyncio
async def test_explicit_dry_run_overrides_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost:5432/testdb")
    mock_audit = AsyncMock(
        return_value={
            "documents": BackfillStats(total_audited=1, reindexed_success=1),
            "external_documents": BackfillStats(total_audited=0),
        }
    )

    with (
        patch("scripts.backfill_pgvector_chunks.create_async_engine") as mock_engine,
        patch("scripts.backfill_pgvector_chunks.audit_and_backfill", mock_audit),
    ):
        mock_engine.return_value.dispose = AsyncMock()
        # Even if --execute is passed, if --dry-run is also passed, dry_run wins
        exit_code = await main(argv=["--execute", "--dry-run", "--limit", "1"])
        assert exit_code == 0
        mock_audit.assert_awaited_once()
        _, kwargs = mock_audit.call_args
        assert kwargs["dry_run"] is True


@pytest.mark.asyncio
async def test_audit_and_backfill_dry_run_never_commits_or_indexes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from scripts.backfill_pgvector_chunks import audit_and_backfill

    from src.models.document import Document

    caplog.set_level(logging.INFO)

    mock_doc = MagicMock(spec=Document)
    mock_doc.id = "doc-test-123"
    mock_doc.filename = "test.pdf"
    mock_doc.storage_key = "documents/test/doc-test-123.pdf"
    mock_doc.chunk_count = None

    mock_session = AsyncMock()
    mock_exec_res = MagicMock()
    mock_exec_res.scalars.return_value.all.return_value = [mock_doc]
    mock_session.execute.return_value = mock_exec_res

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    mock_storage = AsyncMock()
    mock_storage.exists.return_value = True

    mock_vector_store = AsyncMock()
    mock_vector_store.count_document_points.return_value = 0

    mock_engine = MagicMock()

    with (
        patch("scripts.backfill_pgvector_chunks.AsyncSession", mock_session_cls),
        patch("src.storage.object_storage.get_object_storage", return_value=mock_storage),
        patch("src.vector_store.get_vector_store", return_value=mock_vector_store),
    ):
        results = await audit_and_backfill(
            engine=mock_engine,
            target="documents",
            dry_run=True,
        )

        # Assert no mutations occurred
        mock_session.commit.assert_not_called()
        mock_vector_store.index_document.assert_not_called()
        assert results["documents"].total_audited == 1
        assert results["documents"].reindexed_success == 1
        assert "[PROPOSED] Would reindex doc-test-123" in caplog.text

