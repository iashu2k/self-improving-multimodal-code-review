from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.db.models.feedback import (
  AttributionConfidence,
  CommentFeedback,
  FeedbackActorType,
  FeedbackLabel,
  FeedbackSource,
  FeedbackTargetType,
)
from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
from app.feedback.github_events import FeedbackCommandCandidate
from app.feedback.markers import ReviewForgeMarker
from app.feedback.resolver import ResolvedFeedbackTarget
from app.feedback.store import persist_github_command_feedback


async def add_resolved_target(db_session) -> ResolvedFeedbackTarget:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=99,
    head_sha="a" * 40,
    config_version="v1.0",
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

  return ResolvedFeedbackTarget(
    run=run,
    stored_comment=comment,
    marker=ReviewForgeMarker(
      run_id=run.id,
      target_type=FeedbackTargetType.COMMENT,
      file_path="src/client.py",
      line=24,
    ),
  )


def make_candidate() -> FeedbackCommandCandidate:
  return FeedbackCommandCandidate(
    installation_id=42,
    repository_owner="owner",
    repository_name="repo",
    pr_number=99,
    reply_comment_id=700,
    parent_comment_id=600,
    occurred_at=datetime(2026, 8, 15, 19, 41, tzinfo=UTC),
    actor_login="maintainer-user",
    actor_site_admin=False,
    actor_association="MEMBER",
    label=FeedbackLabel.FALSE_POSITIVE,
    free_text="Caller already validates this.",
  )


@pytest.mark.asyncio
async def test_persists_verified_github_command_feedback(db_session) -> None:
  target = await add_resolved_target(db_session)

  result = await persist_github_command_feedback(
    db_session,
    candidate=make_candidate(),
    target=target,
    actor_type=FeedbackActorType.MAINTAINER,
    actor_login_hash="a" * 64,
    delivery_id="github-delivery-001",
  )
  await db_session.commit()

  assert result.created is True
  assert result.feedback.run_id == target.run.id
  assert result.feedback.stored_comment_id == target.stored_comment.id
  assert result.feedback.target_type == FeedbackTargetType.COMMENT
  assert result.feedback.label == FeedbackLabel.FALSE_POSITIVE
  assert result.feedback.free_text == "Caller already validates this."
  assert result.feedback.actor_type == FeedbackActorType.MAINTAINER
  assert result.feedback.actor_login_hash == "a" * 64
  assert result.feedback.source == FeedbackSource.GITHUB_COMMENT_COMMAND
  assert result.feedback.source_event_id == "github-delivery-001"
  assert result.feedback.source_artifact_id == "700"
  assert result.feedback.attribution_confidence == AttributionConfidence.EXACT_MARKER
  assert result.feedback.created_at == datetime(2026, 8, 15, 19, 41, tzinfo=UTC)
  assert result.feedback.recorded_at is not None


@pytest.mark.asyncio
async def test_duplicate_delivery_returns_existing_feedback(db_session) -> None:
  target = await add_resolved_target(db_session)
  candidate = make_candidate()

  first = await persist_github_command_feedback(
    db_session,
    candidate=candidate,
    target=target,
    actor_type=FeedbackActorType.MAINTAINER,
    actor_login_hash="a" * 64,
    delivery_id="github-delivery-duplicate",
  )
  await db_session.commit()

  second = await persist_github_command_feedback(
    db_session,
    candidate=candidate,
    target=target,
    actor_type=FeedbackActorType.MAINTAINER,
    actor_login_hash="a" * 64,
    delivery_id="github-delivery-duplicate",
  )
  await db_session.commit()

  count = await db_session.scalar(select(func.count()).select_from(CommentFeedback))

  assert first.created is True
  assert second.created is False
  assert second.feedback.id == first.feedback.id
  assert count == 1


@pytest.mark.asyncio
async def test_rejects_blank_delivery_id(db_session) -> None:
  target = await add_resolved_target(db_session)

  with pytest.raises(ValueError, match="delivery ID must not be blank"):
    await persist_github_command_feedback(
      db_session,
      candidate=make_candidate(),
      target=target,
      actor_type=FeedbackActorType.MAINTAINER,
      actor_login_hash="a" * 64,
      delivery_id="   ",
    )
