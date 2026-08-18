from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.feedback import CommentFeedback
from app.db.models.review import ReviewRun
from app.db.session import get_db

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackItem(BaseModel):
  id: str
  run_id: int
  stored_comment_id: int | None
  config_version: str
  repository: str
  pr_number: int
  target_type: str
  label: str
  free_text: str | None
  actor_type: str
  actor_login_hash: str | None
  source: str
  source_event_id: str
  source_artifact_id: str | None
  attribution_confidence: str
  created_at: str
  recorded_at: str


class FeedbackPage(BaseModel):
  items: list[FeedbackItem]
  limit: int
  offset: int
  total: int


@router.get("", response_model=FeedbackPage)
async def list_feedback(
  db: Annotated[AsyncSession, Depends(get_db)],
  run_id: int | None = Query(default=None, ge=1),
  config_version: str | None = Query(default=None, max_length=64),
  label: str | None = Query(default=None, max_length=32),
  limit: int = Query(default=50, ge=1, le=200),
  offset: int = Query(default=0, ge=0),
) -> FeedbackPage:
  filters = []

  if run_id is not None:
    filters.append(CommentFeedback.run_id == run_id)
  if config_version is not None:
    filters.append(ReviewRun.config_version == config_version)
  if label is not None:
    filters.append(CommentFeedback.label == label)

  count_query = (
    select(func.count())
    .select_from(CommentFeedback)
    .join(ReviewRun, ReviewRun.id == CommentFeedback.run_id)
    .where(*filters)
  )
  total = await db.scalar(count_query)

  rows = (
    await db.execute(
      select(CommentFeedback, ReviewRun)
      .join(ReviewRun, ReviewRun.id == CommentFeedback.run_id)
      .where(*filters)
      .order_by(CommentFeedback.created_at.desc(), CommentFeedback.id.desc())
      .limit(limit)
      .offset(offset)
    )
  ).all()

  return FeedbackPage(
    items=[
      FeedbackItem(
        id=str(feedback.id),
        run_id=feedback.run_id,
        stored_comment_id=feedback.stored_comment_id,
        config_version=run.config_version,
        repository=f"{run.repo_owner}/{run.repo_name}",
        pr_number=run.pr_number,
        target_type=feedback.target_type,
        label=feedback.label,
        free_text=feedback.free_text,
        actor_type=feedback.actor_type,
        actor_login_hash=feedback.actor_login_hash,
        source=feedback.source,
        source_event_id=feedback.source_event_id,
        source_artifact_id=feedback.source_artifact_id,
        attribution_confidence=feedback.attribution_confidence,
        created_at=feedback.created_at.isoformat(),
        recorded_at=feedback.recorded_at.isoformat(),
      )
      for feedback, run in rows
    ],
    limit=limit,
    offset=offset,
    total=total or 0,
  )
