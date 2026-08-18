import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.config import ConfigurationEvaluation
from app.services.promotion_gate import EvaluationAggregate


async def aggregate_validation_evaluations(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  system: str,
) -> EvaluationAggregate | None:
  rows = (
    await session.scalars(
      select(ConfigurationEvaluation)
      .where(
        ConfigurationEvaluation.configuration_id == configuration_id,
        ConfigurationEvaluation.dataset_split == "validation",
        ConfigurationEvaluation.system == system,
      )
      .order_by(ConfigurationEvaluation.repeat_number)
    )
  ).all()

  if not rows:
    return None

  metric_names = (
    "precision",
    "recall",
    "groundedness",
    "no_comment_accuracy",
  )
  for row in rows:
    if any(getattr(row, metric_name) is None for metric_name in metric_names):
      return None

  return EvaluationAggregate(
    precision=sum(row.precision for row in rows) / len(rows),
    recall=sum(row.recall for row in rows) / len(rows),
    groundedness=sum(row.groundedness for row in rows) / len(rows),
    no_comment_accuracy=(sum(row.no_comment_accuracy for row in rows) / len(rows)),
    safety_policy_failures=sum(row.safety_policy_failures for row in rows),
    validation_repeats=len(rows),
  )
