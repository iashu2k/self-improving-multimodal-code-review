from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models.feedback import (
  AttributionConfidence,
  CommentFeedback,
  FeedbackActorType,
  FeedbackLabel,
  FeedbackSource,
  FeedbackTargetType,
)
from app.db.models.review import ReviewRun, StoredReviewComment


@pytest.mark.asyncio
async def test_comment_feedback_persists_for_inline_comment(db_session) -> None:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=42,
    head_sha="a" * 40,
    config_version="v1.0",
  )
  db_session.add(run)
  await db_session.flush()

  comment = StoredReviewComment(
    run_id=run.id,
    file_path="src/service.py",
    line=24,
    severity="high",
    category="bug_risk",
    title="Null value can reach the API client",
    body="The new path can call the client with a missing token.",
    suggested_fix="Return before invoking the client when token is missing.",
    confidence=0.91,
    status="posted",
  )
  db_session.add(comment)
  await db_session.flush()

  feedback = CommentFeedback(
    run_id=run.id,
    stored_comment_id=comment.id,
    target_type=FeedbackTargetType.COMMENT,
    label=FeedbackLabel.FALSE_POSITIVE,
    free_text="This path is already guarded by the caller.",
    actor_type=FeedbackActorType.MAINTAINER,
    actor_login_hash="a" * 64,
    source=FeedbackSource.MANUAL_REVIEW,
    source_event_id="manual-feedback-001",
    source_artifact_id="review-comment-123",
    attribution_confidence=AttributionConfidence.MANUAL,
    created_at=datetime.now(UTC),
  )
  db_session.add(feedback)
  await db_session.commit()

  persisted = await db_session.get(CommentFeedback, feedback.id)

  assert persisted is not None
  assert persisted.run_id == run.id
  assert persisted.stored_comment_id == comment.id
  assert persisted.label == FeedbackLabel.FALSE_POSITIVE
  assert persisted.target_type == FeedbackTargetType.COMMENT
  assert persisted.recorded_at is not None


@pytest.mark.asyncio
async def test_summary_feedback_does_not_require_inline_comment(db_session) -> None:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=43,
    head_sha="b" * 40,
    config_version="v1.0",
  )
  db_session.add(run)
  await db_session.flush()

  feedback = CommentFeedback(
    run_id=run.id,
    stored_comment_id=None,
    target_type=FeedbackTargetType.REVIEW_SUMMARY,
    label=FeedbackLabel.HELPFUL,
    free_text=None,
    actor_type=FeedbackActorType.DEVELOPER,
    actor_login_hash="b" * 64,
    source=FeedbackSource.GITHUB_REACTION,
    source_event_id="github-delivery-001",
    source_artifact_id="github-review-456",
    attribution_confidence=AttributionConfidence.EXACT_MARKER,
    created_at=datetime.now(UTC),
  )
  db_session.add(feedback)
  await db_session.commit()

  persisted = await db_session.get(CommentFeedback, feedback.id)

  assert persisted is not None
  assert persisted.stored_comment_id is None
  assert persisted.target_type == FeedbackTargetType.REVIEW_SUMMARY
  assert persisted.label == FeedbackLabel.HELPFUL


@pytest.mark.asyncio
async def test_duplicate_source_event_is_rejected(db_session) -> None:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=44,
    head_sha="c" * 40,
    config_version="v1.0",
  )
  db_session.add(run)
  await db_session.flush()

  created_at = datetime.now(UTC)
  first = CommentFeedback(
    run_id=run.id,
    stored_comment_id=None,
    target_type=FeedbackTargetType.REVIEW_SUMMARY,
    label=FeedbackLabel.HELPFUL,
    free_text=None,
    actor_type=FeedbackActorType.DEVELOPER,
    actor_login_hash=None,
    source=FeedbackSource.GITHUB_REACTION,
    source_event_id="github-delivery-duplicate",
    source_artifact_id="github-review-456",
    attribution_confidence=AttributionConfidence.EXACT_MARKER,
    created_at=created_at,
  )
  duplicate = CommentFeedback(
    run_id=run.id,
    stored_comment_id=None,
    target_type=FeedbackTargetType.REVIEW_SUMMARY,
    label=FeedbackLabel.HELPFUL,
    free_text=None,
    actor_type=FeedbackActorType.DEVELOPER,
    actor_login_hash=None,
    source=FeedbackSource.GITHUB_REACTION,
    source_event_id="github-delivery-duplicate",
    source_artifact_id="github-review-456",
    attribution_confidence=AttributionConfidence.EXACT_MARKER,
    created_at=created_at,
  )

  db_session.add(first)
  await db_session.commit()

  db_session.add(duplicate)
  with pytest.raises(IntegrityError):
    await db_session.commit()

  await db_session.rollback()
