import pytest

from autoescrow.core.config import Settings
from autoescrow.infrastructure.redis import RedisClient


@pytest.mark.asyncio
async def test_redis_ping(test_settings: Settings) -> None:
    client = RedisClient(test_settings)
    try:
        await client.ping()
    finally:
        await client.close()
