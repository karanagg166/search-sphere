import time
from collections import defaultdict
from fastapi import HTTPException, Request, status

from src.config import settings


class InMemoryRateLimiter:
    """
    Sliding window rate limiter per client IP or authenticated user ID.
    Enforces rate limits on expensive API endpoints to prevent abuse.
    """

    def __init__(
        self,
        requests_per_minute: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.requests_per_minute = (
            requests_per_minute
            if requests_per_minute is not None
            else settings.RATE_LIMIT_REQUESTS_PER_MINUTE
        )
        self.enabled = (
            enabled if enabled is not None else settings.RATE_LIMIT_ENABLED
        )
        self._history: dict[str, list[float]] = defaultdict(list)

    def _clean_old_requests(self, key: str, now: float) -> None:
        window_start = now - 60.0
        self._history[key] = [t for t in self._history[key] if t > window_start]

    def is_allowed(self, key: str) -> tuple[bool, int, int]:
        """
        Check whether request for `key` is allowed.
        Returns: (is_allowed, remaining_requests, retry_after_seconds)
        """
        if not self.enabled:
            return True, self.requests_per_minute, 0

        now = time.monotonic()
        self._clean_old_requests(key, now)

        current_count = len(self._history[key])
        if current_count >= self.requests_per_minute:
            oldest = self._history[key][0]
            retry_after = max(1, int(60.0 - (now - oldest)))
            return False, 0, retry_after

        self._history[key].append(now)
        remaining = self.requests_per_minute - current_count - 1
        return True, remaining, 0

    async def check(self, request: Request) -> None:
        """
        FastAPI dependency checking rate limit for the incoming request.
        """
        if not self.enabled:
            return

        # Use client IP or authorization token hash as rate limit bucket key
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            key = f"auth:{auth_header[-16:]}"
        else:
            client_ip = request.client.host if request.client else "unknown"
            key = f"ip:{client_ip}"

        allowed, remaining, retry_after = self.is_allowed(key)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please slow down and try again.",
                headers={"Retry-After": str(retry_after)},
            )


# Global rate limiter instance for dependency injection
rate_limiter = InMemoryRateLimiter()
