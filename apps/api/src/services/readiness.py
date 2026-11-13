"""Bounded read-only readiness and stale-pool recovery; never retries writes."""

import asyncio
import time

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

logger = structlog.get_logger()


class DatabaseReadiness:
    def __init__(self):
        self.last_success = None
        self.consecutive_failures = 0
        self._lock = asyncio.Lock()

    async def check(self, sessions, engine, *, timeout=4.0):
        started = time.monotonic()
        try:
            async with asyncio.timeout(timeout):
                # Coalesce concurrent readiness callers; no unbounded DB stampede.
                async with self._lock:
                    if self.last_success and time.monotonic() - self.last_success < 1:
                        return True
                    for attempt in range(2):
                        try:
                            async with asyncio.timeout(1.5):
                                async with sessions() as session:
                                    await session.execute(text("SELECT 1"))
                            self.last_success = time.monotonic()
                            self.consecutive_failures = 0
                            return True
                        except (DBAPIError, OperationalError, TimeoutError):
                            if attempt:
                                raise
                            # Disposes idle pool members, without rolling back or
                            # replaying any application transaction in another session.
                            await engine.dispose()
                            await asyncio.sleep(0.15)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.consecutive_failures += 1
            logger.warning("database_readiness_failed", error_type=type(error).__name__,
                           elapsed_seconds=round(time.monotonic() - started, 3))
        return False


database_readiness = DatabaseReadiness()


def worker_health(worker, enabled):
    if not enabled:
        return "disabled"
    if worker._task is None or worker._task.done():
        return "stopped"
    if worker.last_poll_at is None:
        return "starting"
    from datetime import UTC, datetime

    activity = max(value for value in (worker.last_poll_at, getattr(worker, "last_activity_at", None)) if value is not None)
    if (datetime.now(UTC) - activity).total_seconds() > 120:
        return "stalled"
    return "recovering" if worker.last_error_code else "running"
