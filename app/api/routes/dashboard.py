"""Phase 9 read-only dashboard API.

Five routes backing the audit UI: runs, run detail, evaluation,
feedback, and configuration lifecycle. Read-only by design (decision
31): every mutation stays on the Phase 8 configuration endpoints.
Langfuse deep links reuse the deterministic trace-ID seeds from
app.observability, so no new columns were needed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.config import ConfigurationEvaluation, ReviewConfiguration
from app.db.models.eval import EvalRun
from app.db.models.feedback import CommentFeedback
from app.db.models.review import ReviewRun, ReviewRunEvent, StoredReviewComment
from app.db.session import get_db
from app.observability import get_langfuse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class RunListItem(BaseModel):
  id: int
  repo: str
  pr_number: int
  head_sha: str
  config_version: str
  status: str
  abstain_reason: str | None
  error: str | None
  github_review_id: int | None
  created_at: datetime
  completed_at: datetime | None
  duration_ms: int | None
  comments_posted: int
  comments_suppressed: int
  langfuse_trace_id: str | None
  langfuse_trace_url: str | None


class RunListResponse(BaseModel):
  total: int
  runs: list[RunListItem]


class RunEventItem(BaseModel):
  node: str
  detail: dict[str, Any]
  created_at: datetime


class RunCommentItem(BaseModel):
  id: int
  file_path: str
  line: int
  severity: str
  category: str
  title: str
  body: str
  suggested_fix: str | None
  confidence: float
  status: str
  suppression_reason: str | None


class RunFeedbackItem(BaseModel):
  id: str
  label: str
  target_type: str
  source: str
  stored_comment_id: int | None
  free_text: str | None
  created_at: datetime


class RunDetailResponse(BaseModel):
  run: RunListItem
  events: list[RunEventItem]
  comments: list[RunCommentItem]
  feedback: list[RunFeedbackItem]


class EvalRunItem(BaseModel):
  id: str
  config_version: str
  dataset_split: str
  systems: list[Any]
  status: str
  total_cost_usd: float
  started_at: datetime
  finished_at: datetime | None
  aggregate_metrics: list[Any] | None
  langfuse_trace_id: str | None
  langfuse_trace_url: str | None


class ConfigurationMetricItem(BaseModel):
  configuration_id: str
  config_version: str
  dataset_split: str
  system: str
  repeat_number: int
  precision: float | None
  recall: float | None
  f1: float | None
  groundedness: float | None
  abstention_accuracy: float | None
  no_comment_accuracy: float | None
  safety_policy_failures: int
  created_at: datetime


class EvaluationOverviewResponse(BaseModel):
  eval_runs: list[EvalRunItem]
  configuration_metrics: list[ConfigurationMetricItem]


class FeedbackLabelCount(BaseModel):
  label: str
  count: int


class FeedbackTrendPoint(BaseModel):
  day: str
  label: str
  count: int


class FeedbackOverviewResponse(BaseModel):
  days: int
  by_label: list[FeedbackLabelCount]
  trend: list[FeedbackTrendPoint]
  recent: list[RunFeedbackItem]


class ConfigurationItem(BaseModel):
  id: str
  config_version: str
  parent_version: str | None
  status: str
  change_reason: str
  generator_prompt_version: str
  critic_prompt_version: str
  evaluation_summary: dict[str, Any]
  created_by: str
  created_at: datetime
  evaluated_at: datetime | None
  approved_by: str | None
  approved_at: datetime | None
  promoted_at: datetime | None
  rejected_at: datetime | None
  rejection_reason: str | None
  rolled_back_at: datetime | None
  rollback_reason: str | None
  evaluations_recorded: int


class ConfigurationListResponse(BaseModel):
  total: int
  configurations: list[ConfigurationItem]


def _trace_link(seed: str) -> dict[str, str | None]:
  """Langfuse trace ID and UI URL for a deterministic seed.

  Both None when observability is disabled. The URL additionally needs
  langfuse_project_id configured, because the UI routes through it.
  """
  empty: dict[str, str | None] = {
    "langfuse_trace_id": None,
    "langfuse_trace_url": None,
  }
  client = get_langfuse()
  if client is None:
    return empty
  try:
    trace_id = client.create_trace_id(seed=seed)
  except Exception as exc:
    logger.warning("dashboard_trace_id_failed", error=str(exc)[:200])
    return empty
  url = None
  project_id = getattr(settings, "langfuse_project_id", None)
  if project_id:
    host = settings.langfuse_host.rstrip("/")
    url = f"{host}/project/{project_id}/traces/{trace_id}"
  return {"langfuse_trace_id": trace_id, "langfuse_trace_url": url}


def _duration_ms(run: ReviewRun) -> int | None:
  if run.created_at is None or run.completed_at is None:
    return None
  return int((run.completed_at - run.created_at).total_seconds() * 1000)


def _run_list_item(
  run: ReviewRun,
  *,
  posted: int,
  suppressed: int,
) -> RunListItem:
  return RunListItem(
    id=run.id,
    repo=f"{run.repo_owner}/{run.repo_name}",
    pr_number=run.pr_number,
    head_sha=run.head_sha[:8],
    config_version=run.config_version,
    status=run.status,
    abstain_reason=run.abstain_reason,
    error=run.error,
    github_review_id=run.github_review_id,
    created_at=run.created_at,
    completed_at=run.completed_at,
    duration_ms=_duration_ms(run),
    comments_posted=posted,
    comments_suppressed=suppressed,
    **_trace_link(str(run.id)),
  )


async def _comment_counts(
  db: AsyncSession,
  run_ids: list[int],
) -> dict[int, dict[str, int]]:
  if not run_ids:
    return {}
  rows = (
    await db.execute(
      select(
        StoredReviewComment.run_id,
        StoredReviewComment.status,
        func.count(),
      )
      .where(StoredReviewComment.run_id.in_(run_ids))
      .group_by(StoredReviewComment.run_id, StoredReviewComment.status)
    )
  ).all()
  counts: dict[int, dict[str, int]] = {}
  for run_id, status, n in rows:
    counts.setdefault(run_id, {})[status] = n
  return counts


def _feedback_item(row: CommentFeedback) -> RunFeedbackItem:
  return RunFeedbackItem(
    id=str(row.id),
    label=row.label,
    target_type=row.target_type,
    source=row.source,
    stored_comment_id=row.stored_comment_id,
    free_text=row.free_text,
    created_at=row.created_at,
  )


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
  db: Annotated[AsyncSession, Depends(get_db)],
  status: str | None = None,
  limit: int = Query(50, ge=1, le=200),
  offset: int = Query(0, ge=0),
) -> RunListResponse:
  stmt = select(ReviewRun).order_by(ReviewRun.created_at.desc(), ReviewRun.id.desc())
  count_stmt = select(func.count()).select_from(ReviewRun)
  if status:
    stmt = stmt.where(ReviewRun.status == status)
    count_stmt = count_stmt.where(ReviewRun.status == status)
  total = await db.scalar(count_stmt) or 0
  runs = (await db.scalars(stmt.limit(limit).offset(offset))).all()
  counts = await _comment_counts(db, [run.id for run in runs])
  return RunListResponse(
    total=total,
    runs=[
      _run_list_item(
        run,
        posted=counts.get(run.id, {}).get("posted", 0),
        suppressed=counts.get(run.id, {}).get("suppressed", 0),
      )
      for run in runs
    ],
  )


@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def run_detail(
  run_id: int,
  db: Annotated[AsyncSession, Depends(get_db)],
) -> RunDetailResponse:
  run = await db.get(ReviewRun, run_id)
  if run is None:
    raise HTTPException(status_code=404, detail="review run not found")
  events = (
    await db.scalars(
      select(ReviewRunEvent)
      .where(ReviewRunEvent.run_id == run_id)
      .order_by(ReviewRunEvent.created_at, ReviewRunEvent.id)
    )
  ).all()
  comments = (
    await db.scalars(
      select(StoredReviewComment)
      .where(StoredReviewComment.run_id == run_id)
      .order_by(StoredReviewComment.id)
    )
  ).all()
  feedback = (
    await db.scalars(
      select(CommentFeedback)
      .where(CommentFeedback.run_id == run_id)
      .order_by(CommentFeedback.created_at)
    )
  ).all()
  counts = await _comment_counts(db, [run.id])
  return RunDetailResponse(
    run=_run_list_item(
      run,
      posted=counts.get(run.id, {}).get("posted", 0),
      suppressed=counts.get(run.id, {}).get("suppressed", 0),
    ),
    events=[
      RunEventItem(node=event.node, detail=event.detail, created_at=event.created_at)
      for event in events
    ],
    comments=[
      RunCommentItem(
        id=c.id,
        file_path=c.file_path,
        line=c.line,
        severity=c.severity,
        category=c.category,
        title=c.title,
        body=c.body,
        suggested_fix=c.suggested_fix,
        confidence=c.confidence,
        status=c.status,
        suppression_reason=c.suppression_reason,
      )
      for c in comments
    ],
    feedback=[_feedback_item(row) for row in feedback],
  )


@router.get("/evaluation", response_model=EvaluationOverviewResponse)
async def evaluation_overview(
  db: Annotated[AsyncSession, Depends(get_db)],
  config_version: str | None = None,
  limit: int = Query(20, ge=1, le=100),
) -> EvaluationOverviewResponse:
  run_stmt = select(EvalRun).order_by(EvalRun.started_at.desc()).limit(limit)
  metric_stmt = (
    select(ConfigurationEvaluation, ReviewConfiguration.config_version)
    .join(
      ReviewConfiguration,
      ReviewConfiguration.id == ConfigurationEvaluation.configuration_id,
    )
    .order_by(ConfigurationEvaluation.created_at.desc())
    .limit(200)
  )
  if config_version:
    run_stmt = run_stmt.where(EvalRun.config_version == config_version)
    metric_stmt = metric_stmt.where(ReviewConfiguration.config_version == config_version)
  eval_runs = (await db.scalars(run_stmt)).all()
  metric_rows = (await db.execute(metric_stmt)).all()
  return EvaluationOverviewResponse(
    eval_runs=[
      EvalRunItem(
        id=str(run.id),
        config_version=run.config_version,
        dataset_split=run.dataset_split,
        systems=run.systems,
        status=run.status,
        total_cost_usd=run.total_cost_usd,
        started_at=run.started_at,
        finished_at=run.finished_at,
        aggregate_metrics=run.aggregate_metrics,
        **_trace_link(f"eval-run-{run.id}"),
      )
      for run in eval_runs
    ],
    configuration_metrics=[
      ConfigurationMetricItem(
        configuration_id=str(metric.configuration_id),
        config_version=config_version_value,
        dataset_split=metric.dataset_split,
        system=metric.system,
        repeat_number=metric.repeat_number,
        precision=metric.precision,
        recall=metric.recall,
        f1=metric.f1,
        groundedness=metric.groundedness,
        abstention_accuracy=metric.abstention_accuracy,
        no_comment_accuracy=metric.no_comment_accuracy,
        safety_policy_failures=metric.safety_policy_failures,
        created_at=metric.created_at,
      )
      for metric, config_version_value in metric_rows
    ],
  )


@router.get("/feedback", response_model=FeedbackOverviewResponse)
async def feedback_overview(
  db: Annotated[AsyncSession, Depends(get_db)],
  days: int = Query(30, ge=1, le=365),
) -> FeedbackOverviewResponse:
  cutoff = datetime.now(UTC) - timedelta(days=days)
  label_rows = (
    await db.execute(
      select(CommentFeedback.label, func.count())
      .where(CommentFeedback.created_at >= cutoff)
      .group_by(CommentFeedback.label)
    )
  ).all()
  day_col = func.date(CommentFeedback.created_at)
  trend_rows = (
    await db.execute(
      select(day_col, CommentFeedback.label, func.count())
      .where(CommentFeedback.created_at >= cutoff)
      .group_by(day_col, CommentFeedback.label)
      .order_by(day_col)
    )
  ).all()
  recent = (
    await db.scalars(select(CommentFeedback).order_by(CommentFeedback.created_at.desc()).limit(50))
  ).all()
  return FeedbackOverviewResponse(
    days=days,
    by_label=[FeedbackLabelCount(label=label, count=count) for label, count in label_rows],
    trend=[
      FeedbackTrendPoint(day=str(day), label=label, count=count) for day, label, count in trend_rows
    ],
    recent=[_feedback_item(row) for row in recent],
  )


@router.get("/configurations", response_model=ConfigurationListResponse)
async def list_configurations(
  db: Annotated[AsyncSession, Depends(get_db)],
  status: str | None = None,
  limit: int = Query(50, ge=1, le=200),
  offset: int = Query(0, ge=0),
) -> ConfigurationListResponse:
  stmt = select(ReviewConfiguration).order_by(ReviewConfiguration.created_at.desc())
  count_stmt = select(func.count()).select_from(ReviewConfiguration)
  if status:
    stmt = stmt.where(ReviewConfiguration.status == status)
    count_stmt = count_stmt.where(ReviewConfiguration.status == status)
  total = await db.scalar(count_stmt) or 0
  configs = (await db.scalars(stmt.limit(limit).offset(offset))).all()
  eval_counts: dict[Any, int] = {}
  if configs:
    rows = (
      await db.execute(
        select(ConfigurationEvaluation.configuration_id, func.count())
        .where(ConfigurationEvaluation.configuration_id.in_([c.id for c in configs]))
        .group_by(ConfigurationEvaluation.configuration_id)
      )
    ).all()
    eval_counts = {configuration_id: n for configuration_id, n in rows}
  return ConfigurationListResponse(
    total=total,
    configurations=[
      ConfigurationItem(
        id=str(config.id),
        config_version=config.config_version,
        parent_version=config.parent_version,
        status=config.status,
        change_reason=config.change_reason,
        generator_prompt_version=config.generator_prompt_version,
        critic_prompt_version=config.critic_prompt_version,
        evaluation_summary=config.evaluation_summary,
        created_by=config.created_by,
        created_at=config.created_at,
        evaluated_at=config.evaluated_at,
        approved_by=config.approved_by,
        approved_at=config.approved_at,
        promoted_at=config.promoted_at,
        rejected_at=config.rejected_at,
        rejection_reason=config.rejection_reason,
        rolled_back_at=config.rolled_back_at,
        rollback_reason=config.rollback_reason,
        evaluations_recorded=eval_counts.get(config.id, 0),
      )
      for config in configs
    ],
  )
