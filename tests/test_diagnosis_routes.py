import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
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


async def seed_diagnosed_configuration(db_session) -> ReviewConfiguration:
  config = ReviewConfiguration(
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Diagnosis route test.",
    status=ConfigurationStatus.PENDING,
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )
  db_session.add(config)
  await db_session.flush()

  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=99,
    head_sha="a" * 40,
    config_version=config.config_version,
    status=RunStatus.PUBLISHED,
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
    suggested_fix="Return before invoking the client.",
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
      created_at=datetime(2026, 8, 18, 19, 0, tzinfo=UTC),
    )
  )
  await db_session.commit()

  return config


@pytest.mark.asyncio
async def test_diagnosis_route_returns_failure_clusters(
  client: AsyncClient,
  db_session,
) -> None:
  config = await seed_diagnosed_configuration(db_session)

  async with client:
    response = await client.get(f"/api/v1/configurations/{config.id}/diagnosis")

  assert response.status_code == 200
  payload = response.json()

  assert payload["configuration_id"] == str(config.id)
  assert payload["config_version"] == "v1.2"
  assert payload["total_failures"] == 1
  assert payload["clusters"][0]["category"] == "false_positive"
  assert payload["clusters"][0]["agent_node"] == "review_generator"
  assert payload["clusters"][0]["count"] == 1
  assert payload["clusters"][0]["sources"] == ["github_comment_command"]
  assert payload["clusters"][0]["examples"][0]["free_text"] == ("Caller already validates this.")


@pytest.mark.asyncio
async def test_diagnosis_route_returns_404_for_unknown_configuration(
  client: AsyncClient,
) -> None:
  async with client:
    response = await client.get(f"/api/v1/configurations/{uuid.uuid4()}/diagnosis")

  assert response.status_code == 404
