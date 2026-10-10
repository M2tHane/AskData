"""Small shared text cache with TTL; business modules never touch a Redis client."""

import time
from typing import Protocol

from redis.asyncio import Redis


class TextCache(Protocol):
    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl_seconds: int) -> None: ...


class RedisTextCache:
    def __init__(self, redis: Redis, prefix: str = "cache:") -> None:
        self.redis, self.prefix = redis, prefix

    async def get(self, key: str) -> str | None:
        value = await self.redis.get(self.prefix + key)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        await self.redis.set(self.prefix + key, value, ex=ttl_seconds)


class MemoryTextCache:
    """In-process cache for tests and single-process tools."""

    def __init__(self) -> None:
        self.values: dict[str, tuple[float, str]] = {}

    async def get(self, key: str) -> str | None:
        entry = self.values.get(key)
        if entry is None or entry[0] < time.monotonic():
            return None
        return entry[1]

    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        self.values[key] = (time.monotonic() + ttl_seconds, value)
