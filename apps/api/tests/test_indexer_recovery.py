from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.document_indexer import (
    recover_stale_processing_documents,
    sync_unindexed_documents,
)


@pytest.mark.asyncio
async def test_recover_stale_processing_documents_requeues_within_limit():
    """Documents stranded in processing status under MAX_INDEXING_RETRIES are requeued with next attempt."""
    mock_doc1 = MagicMock()
    mock_doc1.id = "doc-1"
    mock_doc1.status = "processing:attempt:1"
    mock_doc1.updated_at = datetime.now(UTC) - timedelta(seconds=600)

    mock_doc2 = MagicMock()
    mock_doc2.id = "doc-2"
    mock_doc2.status = "processing:attempt:3"
    mock_doc2.updated_at = datetime.now(UTC) - timedelta(seconds=600)

    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_doc1, mock_doc2]
    mock_session.execute.return_value = mock_result

    with patch("src.services.document_indexer.AsyncSessionLocal") as mock_session_local:
        mock_session_local.return_value.__aenter__.return_value = mock_session

        recovered = await recover_stale_processing_documents()

        assert recovered == ["doc-1"]
        assert mock_doc1.status == "uploaded:retry:2"
        assert mock_doc2.status == "failed"
        mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sync_unindexed_documents_recovers_and_indexes():
    """sync_unindexed_documents first recovers stale documents, then triggers indexing."""
    with patch("src.services.document_indexer.recover_stale_processing_documents", new_callable=AsyncMock) as mock_recover, \
         patch("src.services.document_indexer.AsyncSessionLocal") as mock_session_local, \
         patch("src.services.document_indexer.index_document_pipeline", new_callable=AsyncMock) as mock_pipeline:

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("doc-new",)]
        mock_session.execute.return_value = mock_result
        mock_session_local.return_value.__aenter__.return_value = mock_session

        mock_pipeline.return_value = True

        successful = await sync_unindexed_documents()

        mock_recover.assert_awaited_once()
        mock_pipeline.assert_awaited_once_with("doc-new")
        assert successful == ["doc-new"]
