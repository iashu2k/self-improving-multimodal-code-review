from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feedback import (
  AttributionConfidence,
  CommentFeedback,
  FeedbackActorType,
  FeedbackSource,
)
from app.feedback.github_events import FeedbackCommandCandidate
from app.feedback.resolver import ResolvedFeedbackTarget


@dataclass(frozen=True)
class PersistedFeedback:
  feedback: CommentFeedback
  created: bool


async def persist_github_command_feedback(
  session: AsyncSession,
  *,
  candidate: FeedbackCommandCandidate,
  target: ResolvedFeedbackTarget,
  actor_type: FeedbackActorType,
  actor_login_hash: str,
  delivery_id: str,
) -> PersistedFeedback:
  normalized_delivery_id = delivery_id.strip()
  if not normalized_delivery_id:
    raise ValueError("delivery ID must not be blank")

  feedback = CommentFeedback(
    run_id=target.run.id,
    stored_comment_id=target.stored_comment.id,
    target_type=target.marker.target_type,
    label=candidate.label,
    free_text=candidate.free_text,
    actor_type=actor_type,
    actor_login_hash=actor_login_hash,
    source=FeedbackSource.GITHUB_COMMENT_COMMAND,
    source_event_id=normalized_delivery_id,
    source_artifact_id=str(candidate.reply_comment_id),
    attribution_confidence=AttributionConfidence.EXACT_MARKER,
    created_at=candidate.occurred_at,
  )

  try:
    async with session.begin_nested():
      session.add(feedback)
      await session.flush()
  except IntegrityError:
    existing = await session.scalar(
      select(CommentFeedback).where(
        CommentFeedback.source == FeedbackSource.GITHUB_COMMENT_COMMAND,
        CommentFeedback.source_event_id == normalized_delivery_id,
      )
    )
    if existing is None:
      raise
    return PersistedFeedback(feedback=existing, created=False)

  return PersistedFeedback(feedback=feedback, created=True)
