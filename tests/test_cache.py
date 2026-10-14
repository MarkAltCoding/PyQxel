"""Tests for the optional Redis cache and its fallback when Redis fails.

Redis is replaced with ``fakeredis`` so no test needs a server.
"""

import pytest
from fakeredis import FakeAsyncRedis, FakeServer
from pydantic import SecretStr
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core import cache
from app.core.cache import (
    KEY_PREFIX,
    cache_get,
    cache_increment,
    cache_set,
    close_cache,
    configure_cache,
)
from app.core.config import Settings
from app.core.rate_limit import RateLimitExceeded, check_rate_limit, clear_local_counts

pytestmark = pytest.mark.asyncio


class BrokenRedis(FakeAsyncRedis):
    """A Redis client whose every command fails as if the server were down."""

    calls: int = 0

    async def execute_command(self, *args: object, **options: object) -> object:
        """Count the command and fail it."""
        BrokenRedis.calls += 1
        raise RedisConnectionError("Connection refused")


@pytest.fixture
def fake_redis() -> FakeAsyncRedis:
    """Install an empty fake Redis as the cache."""
    client = FakeAsyncRedis(decode_responses=True)
    configure_cache(client)
    return client


async def test_set_then_get_round_trips(fake_redis: FakeAsyncRedis) -> None:
    """A stored value is read back under its key, namespaced in Redis."""
    await cache_set("info:AAPL", '{"symbol": "AAPL"}', ttl_seconds=60)

    assert await cache_get("info:AAPL") == '{"symbol": "AAPL"}'
    assert await fake_redis.get(KEY_PREFIX + "info:AAPL") == '{"symbol": "AAPL"}'


async def test_values_expire_after_their_ttl(fake_redis: FakeAsyncRedis) -> None:
    """Stored values carry the requested time to live, in milliseconds."""
    await cache_set("history:SPY:1y:1d", "[]", ttl_seconds=2.5)

    assert 0 < await fake_redis.pttl(KEY_PREFIX + "history:SPY:1y:1d") <= 2500


async def test_missing_key_is_a_miss(fake_redis: FakeAsyncRedis) -> None:
    """Keys never stored read as ``None``."""
    assert await cache_get("info:NOPE") is None


async def test_disabled_cache_skips_everything() -> None:
    """With caching off, stores are dropped and every read misses."""
    configure_cache(None)

    await cache_set("info:AAPL", "x", ttl_seconds=60)

    assert await cache_get("info:AAPL") is None


async def test_redis_failure_is_a_miss_and_pauses_the_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis error reads as a miss, and Redis is left alone until the pause ends."""
    BrokenRedis.calls = 0
    configure_cache(BrokenRedis())
    clock = [1_000.0]
    monkeypatch.setattr("app.core.cache.time.monotonic", lambda: clock[0])

    assert await cache_get("info:AAPL") is None
    assert BrokenRedis.calls == 1

    await cache_set("info:AAPL", "x", ttl_seconds=60)
    assert await cache_get("info:AAPL") is None
    assert BrokenRedis.calls == 1

    clock[0] += cache.RETRY_AFTER_SECONDS + 1
    await cache_set("info:AAPL", "x", ttl_seconds=60)
    assert BrokenRedis.calls == 2


async def test_client_is_created_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without an explicit client, ``REDIS_URL`` decides whether caching is on."""
    await close_cache()
    monkeypatch.setattr(cache, "get_settings", lambda: Settings(redis_url=None))
    assert cache._get_client() is None

    await close_cache()
    url = SecretStr("redis://localhost:6390/0")
    monkeypatch.setattr(cache, "get_settings", lambda: Settings(redis_url=url))
    client = cache._get_client()
    assert client is not None
    options = client.connection_pool.connection_kwargs
    assert options["port"] == 6390
    # Hosted Redis drops idle connections: they are checked, and a dropped one retried.
    assert options["health_check_interval"] == cache.HEALTH_CHECK_INTERVAL_SECONDS
    assert options["retry"].get_retries() == 1
    assert RedisConnectionError in options["retry_on_error"]

    await close_cache()
    assert cache._client is None


async def test_counters_always_expire(fake_redis: FakeAsyncRedis) -> None:
    """Each count sets the counter's expiry with it, so none is left to grow forever."""
    assert await cache_increment("ratelimit:u:1", ttl_seconds=120) == 1
    assert await cache_increment("ratelimit:u:1", ttl_seconds=120) == 2

    assert 0 < await fake_redis.pttl(KEY_PREFIX + "ratelimit:u:1") <= 120_000


async def test_rate_limits_are_shared_across_instances() -> None:
    """Instances counting in the same Redis enforce one limit between them."""
    clear_local_counts()
    server = FakeServer()
    first, second = (FakeAsyncRedis(server=server, decode_responses=True) for _ in range(2))
    now = 1_000_000.0

    configure_cache(first)
    await check_rate_limit("shared-user", 2, now=now)
    configure_cache(second)
    await check_rate_limit("shared-user", 2, now=now)
    with pytest.raises(RateLimitExceeded):
        await check_rate_limit("shared-user", 2, now=now)
