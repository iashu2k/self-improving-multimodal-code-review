from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feedback import FeedbackTargetType
from app.db.models.review import CommentStatus, ReviewRun, StoredReviewComment
from app.feedback.github_events import FeedbackCommandCandidate
from app.feedback.markers import ReviewForgeMarker, parse_review_forge_marker


@dataclass(frozen=True)
class ResolvedFeedbackTarget:
  run: ReviewRun
  stored_comment: StoredReviewComment
  marker: ReviewForgeMarker


def _is_positive_int(value: object) -> bool:
  return isinstance(value, int) and not isinstance(value, bool) and value > 0


async def resolve_feedback_target(
  session: AsyncSession,
  *,
  candidate: FeedbackCommandCandidate,
  parent_comment: dict,
) -> ResolvedFeedbackTarget | None:
  parent_comment_id = parent_comment.get("id")
  parent_review_id = parent_comment.get("pull_request_review_id")
  parent_body = parent_comment.get("body")
  parent_path = parent_comment.get("path")
  parent_line = parent_comment.get("line")

  if parent_comment_id != candidate.parent_comment_id:
    return None
  if not _is_positive_int(parent_review_id):
    return None
  if not isinstance(parent_body, str):
    return None
  if not isinstance(parent_path, str) or not parent_path:
    return None
  if not _is_positive_int(parent_line):
    return None

  marker = parse_review_forge_marker(parent_body)
  if marker is None or marker.target_type != FeedbackTargetType.COMMENT:
    return None

  if marker.file_path != parent_path or marker.line != parent_line:
    return None

  rows = (
    await session.execute(
      select(ReviewRun, StoredReviewComment)
      .join(StoredReviewComment, StoredReviewComment.run_id == ReviewRun.id)
      .where(
        ReviewRun.id == marker.run_id,
        ReviewRun.repo_owner == candidate.repository_owner,
        ReviewRun.repo_name == candidate.repository_name,
        ReviewRun.pr_number == candidate.pr_number,
        ReviewRun.github_review_id == parent_review_id,
        StoredReviewComment.status == CommentStatus.POSTED,
        StoredReviewComment.file_path == marker.file_path,
        StoredReviewComment.line == marker.line,
      )
    )
  ).all()

  if len(rows) != 1:
    return None

  run, stored_comment = rows[0]
  return ResolvedFeedbackTarget(
    run=run,
    stored_comment=stored_comment,
    marker=marker,
  )
