"""PostgreSQL persistence for eval runs. Mirrors the review-run audit pattern:
one row per run, one per (example, system, attempt), one per judged match.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.eval import EvalExampleResult, EvalMatch, EvalRun
from app.evals.schemas import (
  AggregateMetrics,
  ExampleMetrics,
  MatchRecord,
  SystemName,
)


async def create_run(
  session: AsyncSession,
  *,
  config_version: str,
  dataset_split: str,
  systems: Sequence[SystemName],
) -> EvalRun:
  run = EvalRun(
    id=uuid.uuid4(),
    config_version=config_version,
    dataset_split=dataset_split,
    systems=[s.value for s in systems],
    status="running",
    started_at=datetime.now(UTC),
  )
  session.add(run)
  await session.flush()
  return run


async def record_example_result(
  session: AsyncSession,
  *,
  run_id: uuid.UUID,
  metrics: ExampleMetrics,
  cost_usd: float,
) -> EvalExampleResult:
  row = EvalExampleResult(
    id=uuid.uuid4(),
    run_id=run_id,
    example_id=metrics.example_id,
    system=metrics.system.value,
    attempt=metrics.attempt,
    tp=metrics.tp,
    fp=metrics.fp,
    fn=metrics.fn,
    precision=metrics.precision,
    recall=metrics.recall,
    f1=metrics.f1,
    groundedness=(metrics.grounded_comments / metrics.total_comments)
    if metrics.total_comments
    else None,
    line_validity=(metrics.line_valid_comments / metrics.total_comments)
    if metrics.total_comments
    else None,
    severity_agreement=(
      metrics.severity_agreements / metrics.matched_with_severity
      if metrics.matched_with_severity
      else None
    ),
    expected_empty=metrics.expected_empty,
    predicted_empty=metrics.predicted_empty,
    cost_usd=cost_usd,
  )
  session.add(row)
  await session.flush()
  return row


async def record_matches(
  session: AsyncSession,
  *,
  run_id: uuid.UUID,
  example_result_id: uuid.UUID,
  matches: Sequence[MatchRecord],
) -> None:
  for m in matches:
    session.add(
      EvalMatch(
        id=uuid.uuid4(),
        run_id=run_id,
        example_result_id=example_result_id,
        example_id=m.example_id,
        gold_index=m.gold_index,
        generated_index=m.generated_index,
        verdict=m.verdict.value if m.verdict else None,
        matched=m.matched,
        judge_rationale=m.judge_rationale,
        audited_by_human=m.audited_by_human,
        human_agrees=m.human_agrees,
      )
    )
  await session.flush()


async def finalize_run(
  session: AsyncSession,
  *,
  run: EvalRun,
  aggregates: Sequence[AggregateMetrics],
  status: str = "completed",
) -> None:
  run.status = status
  run.finished_at = datetime.now(UTC)
  run.aggregate_metrics = [a.model_dump(mode="json") for a in aggregates]
  run.total_cost_usd = sum(a.total_cost_usd for a in aggregates)
  await session.flush()


async def load_run(session: AsyncSession, run_id: uuid.UUID) -> EvalRun | None:
  result = await session.execute(select(EvalRun).where(EvalRun.id == run_id))
  return result.scalar_one_or_none()
