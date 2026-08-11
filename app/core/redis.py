from __future__ import annotations

import asyncio
from functools import lru_cache

import redis.asyncio as redis

from app.core.config import settings


@lru_cache
def _redis_for_loop(loop_id: int) -> redis.Redis:
    return redis.from_url(str(settings.redis_url), decode_responses=True)


def get_redis() -> redis.Redis:
    """
    Return a Redis client scoped to the current event loop.

    This avoids reusing the same connection across different asyncio loops
    (which pytest's async tests create), preventing "Future attached to a
    different loop" and "Event loop is closed" errors.
    """
    loop = asyncio.get_running_loop()
    return _redis_for_loop(id(loop))
