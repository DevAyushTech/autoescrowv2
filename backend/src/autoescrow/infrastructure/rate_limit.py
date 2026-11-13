import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

from autoescrow.core.config import Settings
from autoescrow.core.errors import ApiError

logger = logging.getLogger("autoescrow.rate_limit")


class RateLimiter:
    """Fail-closed fixed-window limiter backed by Redis."""

    def __init__(self, redis: Redis, settings: Settings) -> None:
        self._redis = redis
        self._settings = settings

    async def enforce(self, scope: str, identity: str, *, limit: int) -> None:
        window = self._settings.rate_limit_window_seconds
        now = int(time.time())
        bucket = now // window
        key = f"rate-limit:{scope}:{identity}:{bucket}"
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(key)
                pipeline.expire(key, window * 2)
                result = await pipeline.execute()
        except RedisError as exc:
            logger.error(
                "rate_limit.backend_unavailable",
                extra={"scope": scope},
            )
            raise ApiError(
                503,
                "rate_limit_unavailable",
                "Security service temporarily unavailable.",
                headers={"Retry-After": "5"},
            ) from exc

        count = int(result[0])
        if count > limit:
            retry_after = max(1, window - (now % window))
            raise ApiError(
                429,
                "rate_limit_exceeded",
                "Too many requests. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
