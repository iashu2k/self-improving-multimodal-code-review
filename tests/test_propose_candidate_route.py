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


async def seed_config_with_feedback(db_session) -> ReviewConfiguration:
  config = ReviewConfiguration(
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Active config with false-positive feedback.",
    status=ConfigurationStatus.ACTIVE,
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
    thresholds={"minimum_confidence": 0.75, "max_comments_per_pr": 6},
    retrieval_config={"top_k": 8},
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
async def test_propose_candidate_creates_draft_configuration(
  client: AsyncClient,
  db_session,
) -> None:
  source = await seed_config_with_feedback(db_session)

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{source.id}/propose-candidate",
      json={"new_version": "v1.3"},
    )

  assert response.status_code == 201
  payload = response.json()

  assert payload["config_version"] == "v1.3"
  assert payload["parent_version"] == "v1.2"
  assert payload["status"] == "draft"
  assert "false_positive" in payload["change_reason"]
  assert payload["thresholds"]["minimum_confidence"] == 0.78
  assert payload["thresholds"]["max_comments_per_pr"] == 6
  assert payload["generator_prompt_version"] == "generator_v1.2"
  assert payload["critic_prompt_version"] == "critic_v1.1"


@pytest.mark.asyncio
async def test_propose_candidate_returns_404_for_unknown_configuration(
  client: AsyncClient,
) -> None:
  async with client:
    response = await client.post(
      f"/api/v1/configurations/{uuid.uuid4()}/propose-candidate",
      json={"new_version": "v1.3"},
    )

  assert response.status_code == 404


@pytest.mark.asyncio
async def test_propose_candidate_returns_409_for_duplicate_version(
  client: AsyncClient,
  db_session,
) -> None:
  source = await seed_config_with_feedback(db_session)

  async with client:
    first = await client.post(
      f"/api/v1/configurations/{source.id}/propose-candidate",
      json={"new_version": "v1.3"},
    )
    second = await client.post(
      f"/api/v1/configurations/{source.id}/propose-candidate",
      json={"new_version": "v1.3"},
    )

  assert first.status_code == 201
  assert second.status_code == 409


@pytest.mark.asyncio
async def test_propose_candidate_returns_400_when_no_failures(
  client: AsyncClient,
  db_session,
) -> None:
  config = ReviewConfiguration(
    config_version="v1.4",
    parent_version="v1.3",
    change_reason="No failures yet.",
    status=ConfigurationStatus.ACTIVE,
    generator_prompt_version="generator_v1.4",
    critic_prompt_version="critic_v1.3",
  )
  db_session.add(config)
  await db_session.commit()

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{config.id}/propose-candidate",
      json={"new_version": "v1.5"},
    )

  assert response.status_code == 400
  assert "no failure clusters" in response.json()["detail"].lower()
