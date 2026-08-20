"""Smoke tests for the server-rendered dashboard pages.

Same convention as the other route tests: override_db points get_db at
the per-test SQLite session. Pages are read-only HTML over the same
handlers as the JSON API, so rendering without errors on empty and
seeded data is the contract.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models.review import ReviewRun
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def override_db(db_session):
  async def _override():
    yield db_session

  app.dependency_overrides[get_db] = _override
  yield
  app.dependency_overrides.clear()


@pytest.fixture
def client() -> AsyncClient:
  transport = ASGITransport(app=app)
  return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
  "path",
  [
    "/dashboard/configurations",
    "/dashboard/runs",
    "/dashboard/evaluation",
    "/dashboard/feedback",
  ],
)
async def test_page_renders(client: AsyncClient, path: str) -> None:
  async with client:
    response = await client.get(path)

  assert response.status_code == 200
  assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_home_redirects_to_configurations(client: AsyncClient) -> None:
  async with client:
    response = await client.get("/dashboard/", follow_redirects=False)

  assert response.status_code in (307, 308)
  assert response.headers["location"].endswith("/dashboard/configurations")


@pytest.mark.asyncio
async def test_runs_page_shows_seeded_run(client: AsyncClient, db_session) -> None:
  db_session.add(
    ReviewRun(
      repo_owner="octo",
      repo_name=f"ui-{uuid.uuid4().hex[:6]}",
      pr_number=7,
      head_sha=uuid.uuid4().hex * 2,
      config_version="v1.1",
      status="published",
      created_at=datetime.now(UTC),
      completed_at=datetime.now(UTC),
    )
  )
  await db_session.commit()

  async with client:
    response = await client.get("/dashboard/runs")

  assert response.status_code == 200
  assert "octo/ui-" in response.text


@pytest.mark.asyncio
async def test_run_detail_page_404(client: AsyncClient) -> None:
  async with client:
    response = await client.get("/dashboard/runs/999999")

  assert response.status_code == 404
