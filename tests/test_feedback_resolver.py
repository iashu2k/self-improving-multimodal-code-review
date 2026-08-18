from datetime import UTC, datetime

import pytest

from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
from app.feedback.github_events import FeedbackCommandCandidate
from app.feedback.resolver import resolve_feedback_target


def make_candidate(
  *,
  repository_owner: str = "owner",
  repository_name: str = "repo",
  pr_number: int = 99,
) -> FeedbackCommandCandidate:
  from app.db.models.feedback import FeedbackLabel

  return FeedbackCommandCandidate(
    installation_id=42,
    repository_owner=repository_owner,
    repository_name=repository_name,
    pr_number=pr_number,
    reply_comment_id=700,
    parent_comment_id=600,
    occurred_at=datetime(2026, 8, 15, 19, 41, tzinfo=UTC),
    actor_login="maintainer-user",
    actor_association="MEMBER",
    actor_site_admin=False,
    label=FeedbackLabel.FALSE_POSITIVE,
    free_text="Caller already validates this.",
  )


def make_parent_comment(*, review_id: int = 333) -> dict:
  return {
    "id": 600,
    "pull_request_review_id": review_id,
    "path": "src/client.py",
    "line": 24,
    "body": (
      'A bot review finding.\n\n<!-- review-forge {"file":"src/client.py","line":24,"run_id":1} -->'
    ),
  }


async def add_posted_comment(db_session) -> tuple[ReviewRun, StoredReviewComment]:
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
  await db_session.commit()

  return run, comment


@pytest.mark.asyncio
async def test_resolves_verified_parent_comment_to_one_local_comment(db_session) -> None:
  run, comment = await add_posted_comment(db_session)

  resolved = await resolve_feedback_target(
    db_session,
    candidate=make_candidate(),
    parent_comment=make_parent_comment(review_id=run.github_review_id),
  )

  assert resolved is not None
  assert resolved.run.id == run.id
  assert resolved.stored_comment.id == comment.id
  assert resolved.marker.run_id == run.id


@pytest.mark.asyncio
async def test_rejects_parent_comment_from_different_github_review(db_session) -> None:
  await add_posted_comment(db_session)

  resolved = await resolve_feedback_target(
    db_session,
    candidate=make_candidate(),
    parent_comment=make_parent_comment(review_id=999),
  )

  assert resolved is None


@pytest.mark.asyncio
async def test_rejects_parent_comment_with_path_mismatch(db_session) -> None:
  run, _ = await add_posted_comment(db_session)
  parent_comment = make_parent_comment(review_id=run.github_review_id)
  parent_comment["path"] = "src/other.py"

  resolved = await resolve_feedback_target(
    db_session,
    candidate=make_candidate(),
    parent_comment=parent_comment,
  )

  assert resolved is None


@pytest.mark.asyncio
async def test_rejects_run_from_different_pull_request(db_session) -> None:
  run, _ = await add_posted_comment(db_session)

  resolved = await resolve_feedback_target(
    db_session,
    candidate=make_candidate(pr_number=100),
    parent_comment=make_parent_comment(review_id=run.github_review_id),
  )

  assert resolved is None


@pytest.mark.asyncio
async def test_rejects_ambiguous_local_comment_match(db_session) -> None:
  run, _ = await add_posted_comment(db_session)

  db_session.add(
    StoredReviewComment(
      run_id=run.id,
      file_path="src/client.py",
      line=24,
      severity="medium",
      category="maintainability",
      title="A second comment at the same location",
      body="This should make feedback attribution ambiguous.",
      suggested_fix=None,
      confidence=0.8,
      status=CommentStatus.POSTED,
    )
  )
  await db_session.commit()

  resolved = await resolve_feedback_target(
    db_session,
    candidate=make_candidate(),
    parent_comment=make_parent_comment(review_id=run.github_review_id),
  )

  assert resolved is None
