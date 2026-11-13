from typing import cast

from redis.asyncio import Redis

from autoescrow.core.config import Settings


class RedisClient:
    """Owns the application's Redis client."""

    def __init__(self, settings: Settings) -> None:
        self._client = cast(
            Redis,
            Redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=settings.redis_connect_timeout,
                socket_timeout=settings.redis_connect_timeout,
            ),
        )

    @property
    def client(self) -> Redis:
        return self._client

    async def ping(self) -> None:
        await self._client.ping()

    async def close(self) -> None:
        await self._client.aclose()
