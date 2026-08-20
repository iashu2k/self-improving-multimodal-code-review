"""Route tests for the Phase 9 read-only dashboard API.

Follows the existing route-test convention: the autouse override_db
fixture points get_db at the per-test SQLite session, and seeds are
committed so the app's session sees them. Observability is disabled in
tests, so trace links must come back null.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models.config import ConfigurationEvaluation, ReviewConfiguration
from app.db.models.eval import EvalRun
from app.db.models.feedback import CommentFeedback
from app.db.models.review import ReviewRun, ReviewRunEvent, StoredReviewComment
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def override_db(db_session):
  async def _override():
    yield db_session

  app.dependency_overrides[get_db] = _override
  yield
  app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def disable_observability(monkeypatch: pytest.MonkeyPatch) -> None:
  """Dashboard tests never touch Langfuse, even when .env enables it."""
  import app.api.routes.dashboard as dashboard_module

  monkeypatch.setattr(dashboard_module, "get_langfuse", lambda: None)


@pytest.fixture
def client() -> AsyncClient:
  transport = ASGITransport(app=app)
  return AsyncClient(transport=transport, base_url="http://test")


async def _seed_run(db_session, **overrides) -> ReviewRun:
  run = ReviewRun(
    repo_owner="o",
    repo_name=f"r-{uuid.uuid4().hex[:8]}",
    pr_number=1,
    head_sha=uuid.uuid4().hex * 2,
    config_version="v1.1",
    status="published",
    created_at=datetime.now(UTC),
    completed_at=datetime.now(UTC),
    **overrides,
  )
  db_session.add(run)
  await db_session.flush()
  return run


@pytest.mark.asyncio
async def test_list_runs_empty(client: AsyncClient) -> None:
  async with client:
    response = await client.get("/api/v1/dashboard/runs")

  assert response.status_code == 200
  payload = response.json()
  assert payload["total"] >= 0
  assert isinstance(payload["runs"], list)


@pytest.mark.asyncio
async def test_list_runs_includes_comment_counts(client: AsyncClient, db_session) -> None:
  run = await _seed_run(db_session)
  db_session.add(
    StoredReviewComment(
      run_id=run.id,
      file_path="a.py",
      line=3,
      severity="high",
      category="bug_risk",
      title="t",
      body="b",
      suggested_fix=None,
      confidence=0.9,
      status="posted",
    )
  )
  db_session.add(
    StoredReviewComment(
      run_id=run.id,
      file_path="a.py",
      line=7,
      severity="low",
      category="style",
      title="t2",
      body="b2",
      suggested_fix=None,
      confidence=0.4,
      status="suppressed",
      suppression_reason="low_confidence",
    )
  )
  await db_session.commit()

  async with client:
    response = await client.get("/api/v1/dashboard/runs", params={"status": "published"})

  assert response.status_code == 200
  items = [item for item in response.json()["runs"] if item["id"] == run.id]
  assert len(items) == 1
  item = items[0]
  assert item["comments_posted"] == 1
  assert item["comments_suppressed"] == 1
  assert item["repo"] == f"{run.repo_owner}/{run.repo_name}"
  assert item["duration_ms"] is not None
  assert item["langfuse_trace_id"] is None  # observability disabled in tests


@pytest.mark.asyncio
async def test_run_detail_404(client: AsyncClient) -> None:
  async with client:
    response = await client.get("/api/v1/dashboard/runs/999999")

  assert response.status_code == 404


@pytest.mark.asyncio
async def test_run_detail_shape(client: AsyncClient, db_session) -> None:
  run = await _seed_run(db_session)
  db_session.add(ReviewRunEvent(run_id=run.id, node="triage_router", detail={"route": "proceed"}))
  db_session.add(
    CommentFeedback(
      run_id=run.id,
      stored_comment_id=None,
      target_type="review_summary",
      label="helpful",
      free_text="looks right",
      actor_type="maintainer",
      actor_login_hash="h" * 64,
      source="github_reaction",
      source_event_id=f"evt-{uuid.uuid4().hex}",
      attribution_confidence="exact_marker",
      created_at=datetime.now(UTC),
    )
  )
  await db_session.commit()

  async with client:
    response = await client.get(f"/api/v1/dashboard/runs/{run.id}")

  assert response.status_code == 200
  payload = response.json()
  assert payload["run"]["id"] == run.id
  assert [event["node"] for event in payload["events"]] == ["triage_router"]
  assert payload["feedback"][0]["label"] == "helpful"


@pytest.mark.asyncio
async def test_evaluation_overview(client: AsyncClient, db_session) -> None:
  run = EvalRun(
    id=uuid.uuid4(),
    config_version=f"v-dash-{uuid.uuid4().hex[:8]}",
    dataset_split="validation",
    systems=["final_agent"],
    status="completed",
    aggregate_metrics=[{"system": "final_agent", "precision": 0.5}],
    total_cost_usd=1.23,
    started_at=datetime.now(UTC),
    finished_at=datetime.now(UTC),
  )
  db_session.add(run)
  await db_session.commit()

  async with client:
    response = await client.get(
      "/api/v1/dashboard/evaluation", params={"config_version": run.config_version}
    )

  assert response.status_code == 200
  payload = response.json()
  runs = [item for item in payload["eval_runs"] if item["id"] == str(run.id)]
  assert len(runs) == 1
  assert runs[0]["aggregate_metrics"][0]["precision"] == 0.5
  assert runs[0]["langfuse_trace_id"] is None


@pytest.mark.asyncio
async def test_feedback_overview_counts(client: AsyncClient, db_session) -> None:
  run = await _seed_run(db_session)
  for label in ("helpful", "helpful", "false_positive"):
    db_session.add(
      CommentFeedback(
        run_id=run.id,
        stored_comment_id=None,
        target_type="comment",
        label=label,
        free_text=None,
        actor_type="developer",
        actor_login_hash="h" * 64,
        source="manual_review",
        source_event_id=f"evt-{uuid.uuid4().hex}",
        attribution_confidence="manual",
        created_at=datetime.now(UTC),
      )
    )
  await db_session.commit()

  async with client:
    response = await client.get("/api/v1/dashboard/feedback")

  assert response.status_code == 200
  payload = response.json()
  counts = {item["label"]: item["count"] for item in payload["by_label"]}
  assert counts.get("helpful", 0) >= 2
  assert counts.get("false_positive", 0) >= 1
  assert len(payload["recent"]) >= 3


@pytest.mark.asyncio
async def test_configurations_list(client: AsyncClient, db_session) -> None:
  config = ReviewConfiguration(
    id=uuid.uuid4(),
    config_version=f"v-dash-{uuid.uuid4().hex[:8]}",
    parent_version=None,
    change_reason="dashboard route test",
    status="draft",
    generator_prompt_version="generator_v1.1",
    critic_prompt_version="critic_v1.1",
  )
  db_session.add(config)
  await db_session.flush()
  db_session.add(
    ConfigurationEvaluation(
      id=uuid.uuid4(),
      configuration_id=config.id,
      dataset_split="validation",
      system="final_agent",
      repeat_number=1,
      precision=0.4,
      recall=0.3,
      f1=0.35,
    )
  )
  await db_session.commit()

  async with client:
    response = await client.get("/api/v1/dashboard/configurations")

  assert response.status_code == 200
  payload = response.json()
  items = [item for item in payload["configurations"] if item["id"] == str(config.id)]
  assert len(items) == 1
  assert items[0]["status"] == "draft"
  assert items[0]["evaluations_recorded"] == 1


@pytest.mark.asyncio
async def test_configurations_status_filter(client: AsyncClient) -> None:
  async with client:
    response = await client.get("/api/v1/dashboard/configurations", params={"status": "active"})

  assert response.status_code == 200
  for item in response.json()["configurations"]:
    assert item["status"] == "active"
