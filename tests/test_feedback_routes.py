from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models.feedback import (
  AttributionConfidence,
  CommentFeedback,
  FeedbackActorType,
  FeedbackLabel,
  FeedbackSource,
  FeedbackTargetType,
)
from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
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


async def seed_feedback(db_session) -> tuple[ReviewRun, StoredReviewComment]:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=99,
    head_sha="a" * 40,
    config_version="v1.2",
    status=RunStatus.PUBLISHED,
    github_review_id=333,
  )
  db_session.add(run)
  await db_session.flush()

  comment = StoredReviewComment(
    run_id=run.id,
    file_path="src/client.py",
    line=24,
    severity="high",
    category="bug_risk",
    title="Missing null guard",
    body="The client can receive a missing token.",
    suggested_fix="Return before invoking the client when token is missing.",
    confidence=0.91,
    status=CommentStatus.POSTED,
  )
  db_session.add(comment)
  await db_session.flush()

  db_session.add(
    CommentFeedback(
      run_id=run.id,
      stored_comment_id=comment.id,
      target_type=FeedbackTargetType.COMMENT,
      label=FeedbackLabel.FALSE_POSITIVE,
      free_text="Caller already validates this.",
      actor_type=FeedbackActorType.MAINTAINER,
      actor_login_hash="a" * 64,
      source=FeedbackSource.GITHUB_COMMENT_COMMAND,
      source_event_id="github-delivery-001",
      source_artifact_id="700",
      attribution_confidence=AttributionConfidence.EXACT_MARKER,
      created_at=datetime(2026, 8, 18, 17, 50, tzinfo=UTC),
    )
  )
  await db_session.commit()

  return run, comment


@pytest.mark.asyncio
async def test_list_feedback_returns_attributed_comment(
  client: AsyncClient,
  db_session,
) -> None:
  run, comment = await seed_feedback(db_session)

  async with client:
    response = await client.get("/api/v1/feedback")

  assert response.status_code == 200
  payload = response.json()

  assert payload["total"] == 1
  assert len(payload["items"]) == 1

  item = payload["items"][0]
  assert item["run_id"] == run.id
  assert item["stored_comment_id"] == comment.id
  assert item["config_version"] == "v1.2"
  assert item["repository"] == "owner/repo"
  assert item["pr_number"] == 99
  assert item["label"] == "false_positive"
  assert item["free_text"] == "Caller already validates this."
  assert item["actor_type"] == "maintainer"
  assert item["actor_login_hash"] == "a" * 64
  assert item["source"] == "github_comment_command"
  assert item["source_event_id"] == "github-delivery-001"
  assert item["source_artifact_id"] == "700"
  assert item["attribution_confidence"] == "exact_marker"
  assert item["created_at"].startswith("2026-08-18T17:50:00")
  assert "recorded_at" in item


@pytest.mark.asyncio
async def test_list_feedback_filters_by_run_id(
  client: AsyncClient,
  db_session,
) -> None:
  run, _ = await seed_feedback(db_session)

  async with client:
    response = await client.get(f"/api/v1/feedback?run_id={run.id}")

  assert response.status_code == 200
  assert response.json()["total"] == 1
  assert response.json()["items"][0]["run_id"] == run.id


@pytest.mark.asyncio
async def test_list_feedback_filters_by_config_version(
  client: AsyncClient,
  db_session,
) -> None:
  await seed_feedback(db_session)

  async with client:
    response = await client.get("/api/v1/feedback?config_version=v1.2")

  assert response.status_code == 200
  assert response.json()["total"] == 1
  assert response.json()["items"][0]["config_version"] == "v1.2"


@pytest.mark.asyncio
async def test_list_feedback_filters_by_label(
  client: AsyncClient,
  db_session,
) -> None:
  await seed_feedback(db_session)

  async with client:
    response = await client.get("/api/v1/feedback?label=false_positive")

  assert response.status_code == 200
  assert response.json()["total"] == 1
  assert response.json()["items"][0]["label"] == "false_positive"


@pytest.mark.asyncio
async def test_list_feedback_returns_empty_page_for_unknown_run(
  client: AsyncClient,
) -> None:
  async with client:
    response = await client.get("/api/v1/feedback?run_id=999999")

  assert response.status_code == 200
  assert response.json() == {
    "items": [],
    "limit": 50,
    "offset": 0,
    "total": 0,
  }
