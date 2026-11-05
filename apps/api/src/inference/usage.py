import hashlib
from datetime import UTC, datetime, timedelta

from src.config import settings
from src.db import AsyncSessionLocal
from src.inference.policy import (
    ProviderError,
    ProviderQuotaExceededError,
    ProviderRateLimitError,
)
from src.models.provider_usage import ProviderUsage

# Conservative application limits below the documented trial endpoint limits.
MINUTE_LIMITS = {"embed": 1500, "rerank": 8, "chat": 15, "tokenize": 80}


class CohereUsageBudget:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.sessions = session_factory

    async def reserve(self, api_key: str, purpose: str, units: int = 1) -> None:
        if purpose not in MINUTE_LIMITS or units <= 0:
            raise ValueError("Invalid inference budget reservation")
        now = datetime.now(UTC)
        fingerprint = hashlib.sha256(api_key.encode()).hexdigest()
        minute_units = units if purpose == "embed" else 1
        month_end = (now.replace(day=28) + timedelta(days=4)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        windows = [
            (
                f"cohere:{fingerprint}:month:{now:%Y-%m}",
                min(settings.COHERE_MONTHLY_CALL_LIMIT, 900),
                1,
                month_end,
            ),
            (
                f"cohere:{fingerprint}:minute:{purpose}:{now:%Y-%m-%dT%H:%M}",
                MINUTE_LIMITS[purpose],
                minute_units,
                now + timedelta(minutes=2),
            ),
        ]
        try:
            async with self.sessions() as db, db.begin():
                if db.get_bind().dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                else:
                    from sqlalchemy.dialects.sqlite import insert
                for bucket, limit, increment, expiry in windows:
                    if increment > limit:
                        raise ProviderRateLimitError(
                            "Provider input rate limit reached."
                        )
                    statement = insert(ProviderUsage).values(
                        bucket=bucket, used=increment, expires_at=expiry
                    )
                    statement = statement.on_conflict_do_update(
                        index_elements=["bucket"],
                        set_={
                            "used": ProviderUsage.used + increment,
                            "updated_at": now,
                        },
                        where=ProviderUsage.used + increment <= limit,
                    ).returning(ProviderUsage.used)
                    if (await db.execute(statement)).scalar_one_or_none() is None:
                        if ":month:" in bucket:
                            raise ProviderQuotaExceededError(
                                "Application trial quota reached."
                            )
                        raise ProviderRateLimitError(
                            "Provider request rate limit reached."
                        )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("Provider budget verification is unavailable.") from exc
