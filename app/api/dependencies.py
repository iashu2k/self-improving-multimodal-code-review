from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from fastapi import Request

from app.core.config import settings


async def get_arq_pool(request: Request) -> ArqRedis:
    """Lazily create and cache the ARQ pool on app.state.

    Lazy (not lifespan) so tests using ASGITransport — which skips
    lifespan events — can inject a fake pool on app.state directly.
    """
    pool = getattr(request.app.state, "arq_pool", None)
    if pool is None:
        if not settings.redis_url:
            raise RuntimeError("REDIS_URL is not configured")
        pool = await create_pool(RedisSettings.from_dsn(str(settings.redis_url)))
        request.app.state.arq_pool = pool
    return pool
