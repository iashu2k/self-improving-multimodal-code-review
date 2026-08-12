from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


@lru_cache
def get_engine():
  if not settings.database_url:
    raise RuntimeError("DATABASE_URL is not configured")
  return create_async_engine(str(settings.database_url), pool_pre_ping=True)


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
  return async_sessionmaker(get_engine(), class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
  async with get_session_maker()() as session:
    yield session
