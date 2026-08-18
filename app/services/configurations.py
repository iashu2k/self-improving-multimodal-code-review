import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.config import (
  ConfigurationEvaluation,
  ConfigurationStatus,
  ReviewConfiguration,
)


class ConfigurationConflictError(ValueError):
  """Raised when a configuration version violates uniqueness."""


async def create_configuration_candidate(
  session: AsyncSession,
  *,
  config_version: str,
  parent_version: str | None,
  change_reason: str,
  generator_prompt_version: str,
  critic_prompt_version: str,
  router_rules: dict | None = None,
  thresholds: dict | None = None,
  model_versions: dict | None = None,
  retrieval_config: dict | None = None,
  repair_policy: dict | None = None,
  evaluation_summary: dict | None = None,
  created_by: str = "manual",
) -> ReviewConfiguration:
  normalized_version = config_version.strip()
  if not normalized_version:
    raise ValueError("config_version must not be blank")
  if not change_reason.strip():
    raise ValueError("change_reason must not be blank")
  if not generator_prompt_version.strip():
    raise ValueError("generator_prompt_version must not be blank")
  if not critic_prompt_version.strip():
    raise ValueError("critic_prompt_version must not be blank")

  config = ReviewConfiguration(
    config_version=normalized_version,
    parent_version=parent_version.strip() if parent_version else None,
    change_reason=change_reason,
    status=ConfigurationStatus.DRAFT,
    router_rules=router_rules or {},
    generator_prompt_version=generator_prompt_version,
    critic_prompt_version=critic_prompt_version,
    thresholds=thresholds or {},
    model_versions=model_versions or {},
    retrieval_config=retrieval_config or {},
    repair_policy=repair_policy or {},
    evaluation_summary=evaluation_summary or {},
    created_by=created_by,
  )

  try:
    async with session.begin_nested():
      session.add(config)
      await session.flush()
  except IntegrityError as exc:
    raise ConfigurationConflictError(
      f"configuration version {normalized_version!r} already exists"
    ) from exc

  return config


async def record_configuration_evaluation(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  dataset_split: str,
  system: str,
  repeat_number: int,
  precision: float | None = None,
  recall: float | None = None,
  f1: float | None = None,
  groundedness: float | None = None,
  abstention_accuracy: float | None = None,
  no_comment_accuracy: float | None = None,
  safety_policy_failures: int = 0,
  metrics: dict | None = None,
) -> ConfigurationEvaluation:
  if dataset_split == "holdout":
    raise ValueError("holdout evaluation cannot be recorded as a candidate signal")
  if repeat_number < 1:
    raise ValueError("repeat_number must be at least 1")
  if safety_policy_failures < 0:
    raise ValueError("safety_policy_failures cannot be negative")

  config = await session.get(ReviewConfiguration, configuration_id)
  if config is None:
    raise ValueError("configuration does not exist")

  evaluation = ConfigurationEvaluation(
    configuration_id=configuration_id,
    dataset_split=dataset_split,
    system=system,
    repeat_number=repeat_number,
    precision=precision,
    recall=recall,
    f1=f1,
    groundedness=groundedness,
    abstention_accuracy=abstention_accuracy,
    no_comment_accuracy=no_comment_accuracy,
    safety_policy_failures=safety_policy_failures,
    metrics=metrics or {},
  )
  session.add(evaluation)
  await session.flush()

  return evaluation


async def get_active_configuration(
  session: AsyncSession,
) -> ReviewConfiguration | None:
  return await session.scalar(
    select(ReviewConfiguration)
    .where(ReviewConfiguration.status == ConfigurationStatus.ACTIVE)
    .order_by(ReviewConfiguration.promoted_at.desc(), ReviewConfiguration.created_at.desc())
    .limit(1)
  )
