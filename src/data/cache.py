"""Redis cache layer for real-time data."""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


class Cache:
    """Redis-based cache for real-time state. Degrades gracefully if Redis unavailable."""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self.redis_url = redis_url
        self._redis = None
        self._connected = False

    async def connect(self) -> bool:
        """Attempt to connect to Redis."""
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self.redis_url, decode_responses=True)
            await self._redis.ping()
            self._connected = True
            logger.info("Connected to Redis at %s", self.redis_url)
            return True
        except Exception as e:
            logger.warning("Redis unavailable (%s) — running without cache", e)
            self._connected = False
            return False

    async def set_json(self, key: str, value: dict, ttl: int = 300) -> None:
        """Cache a JSON-serializable value."""
        if not self._connected:
            return
        try:
            await self._redis.set(key, json.dumps(value, default=str), ex=ttl)
        except Exception as e:
            logger.debug("Redis set failed for %s: %s", key, e)

    async def get_json(self, key: str) -> dict | None:
        """Retrieve a cached JSON value."""
        if not self._connected:
            return None
        try:
            raw = await self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.debug("Redis get failed for %s: %s", key, e)
            return None

    async def publish(self, channel: str, message: dict) -> None:
        """Publish to a Redis pub/sub channel."""
        if not self._connected:
            return
        try:
            await self._redis.publish(channel, json.dumps(message, default=str))
        except Exception as e:
            logger.debug("Redis publish failed on %s: %s", channel, e)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis and self._connected:
            await self._redis.close()
