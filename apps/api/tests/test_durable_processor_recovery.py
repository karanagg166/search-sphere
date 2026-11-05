from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.document import Document
from src.services.durable_processor import DurableDocumentProcessor


@pytest.mark.asyncio
async def test_recover_stranded_jobs_requeues_and_increments_attempt() -> None:
    processor = DurableDocumentProcessor()

    doc = MagicMock(spec=Document)
    doc.id = "doc-stranded-1"
    doc.status = "proc:1730000000:1"
    doc.updated_at = datetime.now(UTC) - timedelta(seconds=60)

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [doc]
    mock_session.execute.return_value = mock_res

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        recovered = await processor.recover_stranded_jobs(force_all=True)

        assert recovered == ["doc-stranded-1"]
        assert doc.status == "uploaded:retry:2"
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_stranded_jobs_marks_terminal_when_max_retries_exceeded() -> None:
    processor = DurableDocumentProcessor()

    doc = MagicMock(spec=Document)
    doc.id = "doc-stranded-max"
    doc.status = "proc:1730000000:3"
    doc.updated_at = datetime.now(UTC) - timedelta(seconds=400)

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [doc]
    mock_session.execute.return_value = mock_res

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        recovered = await processor.recover_stranded_jobs(force_all=False)

        assert recovered == []
        assert doc.status == "failed:max_retries_exceeded"
        mock_session.commit.assert_awaited_once()
