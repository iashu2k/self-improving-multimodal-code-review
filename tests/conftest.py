import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.models import (  # noqa: F401
  CommentFeedback,
  ConfigurationEvaluation,
  ReviewConfiguration,
  ReviewRun,
  StoredReviewComment,
  WebhookEvent,
)


class FakeStructuredClient:
  """Key results by schema_name: 'route_decision' | 'review_result' | 'qa_result'.
  A list value pops one result per call (for repair-loop sequences)."""

  def __init__(self, results: dict | None = None) -> None:
    self.results = results or {}
    self.calls: list[dict] = []

  async def chat_structured(self, *, model, schema_name, json_schema, messages):
    self.calls.append({"model": model, "schema_name": schema_name, "messages": messages})
    result = self.results[schema_name]
    if isinstance(result, list):
      result = result.pop(0)

    class _Response:
      content = result.model_dump(mode="json")

    return _Response()


@pytest.fixture
async def session_maker():
  engine = create_async_engine("sqlite+aiosqlite:///:memory:")
  async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
  maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
  yield maker
  await engine.dispose()


@pytest.fixture
async def db_session(session_maker):
  async with session_maker() as session:
    yield session
