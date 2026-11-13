import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import OperationalError

from src.services.readiness import DatabaseReadiness, worker_health


def sessions_with(execute):
    session = SimpleNamespace(execute=execute)
    context = AsyncMock()
    context.__aenter__.return_value = session
    return lambda: context


@pytest.mark.asyncio
async def test_stale_pool_recovers_once_without_replaying_application_writes():
    execute = AsyncMock(side_effect=[OperationalError("SELECT 1", {}, Exception("synthetic")), None])
    engine = SimpleNamespace(dispose=AsyncMock())
    probe = DatabaseReadiness()
    assert await probe.check(sessions_with(execute), engine)
    assert execute.await_count == 2
    engine.dispose.assert_awaited_once()
    assert all(str(call.args[0]) == "SELECT 1" for call in execute.call_args_list)


@pytest.mark.asyncio
async def test_unavailable_database_and_timeout_fail_honestly():
    async def slow(*args):
        await asyncio.sleep(60)

    probe = DatabaseReadiness()
    assert not await probe.check(sessions_with(AsyncMock(side_effect=slow)), SimpleNamespace(dispose=AsyncMock()), timeout=0.02)
    assert probe.consecutive_failures == 1 and probe.last_success is None


@pytest.mark.asyncio
async def test_concurrent_readiness_calls_share_a_bounded_successful_query():
    execute = AsyncMock()
    probe = DatabaseReadiness()
    assert all(await asyncio.gather(*(probe.check(sessions_with(execute), SimpleNamespace(dispose=AsyncMock())) for _ in range(8))))
    assert execute.await_count == 1


def test_active_job_heartbeat_is_not_reported_as_stalled_polling():
    worker = SimpleNamespace(_task=SimpleNamespace(done=lambda: False), last_poll_at=datetime.now(UTC) - timedelta(minutes=5),
                             last_activity_at=datetime.now(UTC), last_error_code=None)
    assert worker_health(worker, True) == "running"
    worker.last_activity_at = None
    assert worker_health(worker, True) == "stalled"
    assert worker_health(worker, False) == "disabled"
