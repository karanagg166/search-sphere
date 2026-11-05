from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.models.document import Document
from src.services.durable_processor import DurableDocumentProcessor


@pytest.mark.asyncio
async def test_deployment_handoff_living_worker_not_reclaimed_by_new_worker() -> None:
    """
    During a deployment handoff, worker A is still actively processing a job.
    A newly booted worker B must NOT reclaim worker A's active lease.
    """
    worker_a = DurableDocumentProcessor(worker_id="w_node_a")
    worker_b = DurableDocumentProcessor(worker_id="w_node_b")

    future_expiry = int(time.time() + 200)
    active_lease = f"proc:{worker_a.worker_id}:{future_expiry}:1"

    doc = MagicMock(spec=Document)
    doc.id = "doc-active-handoff"
    doc.status = active_lease
    doc.updated_at = datetime.now(UTC)

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [doc]
    mock_session.execute.return_value = mock_res

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        # Worker B starts up and runs standard recovery (force_all=False)
        recovered = await worker_b.recover_stranded_jobs(force_all=False)

        # Worker A's active lease is NOT reclaimed
        assert recovered == []
        assert doc.status == active_lease


@pytest.mark.asyncio
async def test_expired_lease_recovered_after_timeout() -> None:
    """When a worker crashes or abandons a lease and it expires, it is safely reclaimed."""
    worker = DurableDocumentProcessor(worker_id="w_recovering")

    past_expiry = int(time.time() - 30)
    expired_lease = f"proc:w_crashed:{past_expiry}:1"

    doc = MagicMock(spec=Document)
    doc.id = "doc-crashed-worker"
    doc.status = expired_lease
    doc.updated_at = datetime.now(UTC) - timedelta(seconds=350)

    mock_session = AsyncMock()
    mock_select_res = MagicMock()
    mock_select_res.scalars.return_value.all.return_value = [doc]

    mock_update_res = MagicMock()
    mock_update_res.rowcount = 1

    # First call is select, second is update
    mock_session.execute.side_effect = [mock_select_res, mock_update_res]

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        recovered = await worker.recover_stranded_jobs(force_all=False)

        assert recovered == ["doc-crashed-worker"]
        mock_session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_concurrent_recovery_attempts_cas_prevents_duplicate_reclaim() -> None:
    """If two workers concurrently attempt to reclaim the same expired job, only one succeeds."""
    worker_1 = DurableDocumentProcessor(worker_id="w_1")
    worker_2 = DurableDocumentProcessor(worker_id="w_2")

    past_expiry = int(time.time() - 60)
    expired_lease = f"proc:w_dead:{past_expiry}:1"

    doc = MagicMock(spec=Document)
    doc.id = "doc-race-condition"
    doc.status = expired_lease
    doc.updated_at = datetime.now(UTC) - timedelta(seconds=400)

    # Session for Worker 1: CAS update succeeds (rowcount=1)
    mock_session_1 = AsyncMock()
    mock_res_1 = MagicMock()
    mock_res_1.scalars.return_value.all.return_value = [doc]
    mock_update_1 = MagicMock()
    mock_update_1.rowcount = 1
    mock_session_1.execute.side_effect = [mock_res_1, mock_update_1]

    # Session for Worker 2: CAS update fails because Worker 1 already changed status (rowcount=0)
    mock_session_2 = AsyncMock()
    mock_res_2 = MagicMock()
    mock_res_2.scalars.return_value.all.return_value = [doc]
    mock_update_2 = MagicMock()
    mock_update_2.rowcount = 0
    mock_session_2.execute.side_effect = [mock_res_2, mock_update_2]

    cls_1 = MagicMock()
    cls_1.return_value.__aenter__.return_value = mock_session_1
    cls_1.return_value.__aexit__.return_value = None

    cls_2 = MagicMock()
    cls_2.return_value.__aenter__.return_value = mock_session_2
    cls_2.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", cls_1):
        rec_1 = await worker_1.recover_stranded_jobs(force_all=False)

    with patch("src.services.durable_processor.AsyncSessionLocal", cls_2):
        rec_2 = await worker_2.recover_stranded_jobs(force_all=False)

    assert rec_1 == ["doc-race-condition"]
    assert rec_2 == []


@pytest.mark.asyncio
async def test_stale_worker_prevented_from_overwriting_completed_job() -> None:
    """If a worker's lease expires and another worker completes the job, the stale worker cannot overwrite it."""
    stale_worker = DurableDocumentProcessor(worker_id="w_stale")

    doc_id = "doc-stolen"
    stale_lease = f"proc:{stale_worker.worker_id}:{int(time.time() - 100)}:1"

    # Simulate DB where status is already 'indexed' by another worker, so CAS with stale_lease affects 0 rows
    mock_session = AsyncMock()
    mock_update_res = MagicMock()
    mock_update_res.rowcount = 0
    mock_session.execute.return_value = mock_update_res

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        # Attempt completion with stale lease token
        success = await stale_worker.complete_job(doc_id, chunk_count=5, expected_lease=stale_lease)
        assert success is False


@pytest.mark.asyncio
async def test_recover_stranded_jobs_marks_terminal_when_max_retries_exceeded() -> None:
    """Expired leases that have exhausted retries are dead-lettered to failed:max_retries_exceeded."""
    processor = DurableDocumentProcessor(worker_id="w_terminal")

    past_expiry = int(time.time() - 30)
    doc = MagicMock(spec=Document)
    doc.id = "doc-stranded-max"
    doc.status = f"proc:w_old:{past_expiry}:3"
    doc.updated_at = datetime.now(UTC) - timedelta(seconds=400)

    mock_session = AsyncMock()
    mock_res = MagicMock()
    mock_res.scalars.return_value.all.return_value = [doc]
    mock_update_res = MagicMock()
    mock_update_res.rowcount = 1
    mock_session.execute.side_effect = [mock_res, mock_update_res]

    mock_session_cls = MagicMock()
    mock_session_cls.return_value.__aenter__.return_value = mock_session
    mock_session_cls.return_value.__aexit__.return_value = None

    with patch("src.services.durable_processor.AsyncSessionLocal", mock_session_cls):
        recovered = await processor.recover_stranded_jobs(force_all=False)

        assert recovered == []
        mock_session.commit.assert_awaited()
