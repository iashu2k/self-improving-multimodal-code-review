import pytest

from app.services.configurations import (
  create_configuration_candidate,
  record_configuration_evaluation,
)
from app.services.promotion_data import aggregate_validation_evaluations
from app.services.promotion_gate import MINIMUM_VALIDATION_REPEATS


@pytest.mark.asyncio
async def test_aggregates_repeated_validation_evaluations(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Aggregate candidate validation metrics.",
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )

  await record_configuration_evaluation(
    db_session,
    configuration_id=config.id,
    dataset_split="validation",
    system="final_agent",
    repeat_number=1,
    precision=0.10,
    recall=0.20,
    f1=0.13,
    groundedness=0.90,
    abstention_accuracy=0.70,
    no_comment_accuracy=1.0,
    safety_policy_failures=0,
  )
  await record_configuration_evaluation(
    db_session,
    configuration_id=config.id,
    dataset_split="validation",
    system="final_agent",
    repeat_number=2,
    precision=0.14,
    recall=0.24,
    f1=0.17,
    groundedness=0.94,
    abstention_accuracy=0.80,
    no_comment_accuracy=1.0,
    safety_policy_failures=0,
  )

  # Development data must not contaminate the validation gate.
  await record_configuration_evaluation(
    db_session,
    configuration_id=config.id,
    dataset_split="development",
    system="final_agent",
    repeat_number=1,
    precision=1.0,
    recall=1.0,
    f1=1.0,
    groundedness=1.0,
    abstention_accuracy=1.0,
    no_comment_accuracy=1.0,
    safety_policy_failures=0,
  )
  await db_session.commit()

  aggregate = await aggregate_validation_evaluations(
    db_session,
    configuration_id=config.id,
    system="final_agent",
  )

  assert aggregate is not None
  assert aggregate.validation_repeats == 2
  assert aggregate.precision == pytest.approx(0.12)
  assert aggregate.recall == pytest.approx(0.22)
  assert aggregate.groundedness == pytest.approx(0.92)
  assert aggregate.no_comment_accuracy == pytest.approx(1.0)
  assert aggregate.safety_policy_failures == 0


@pytest.mark.asyncio
async def test_returns_none_without_validation_evaluations(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.3",
    parent_version="v1.2",
    change_reason="No validation evaluation yet.",
    generator_prompt_version="generator_v1.3",
    critic_prompt_version="critic_v1.2",
  )
  await db_session.commit()

  aggregate = await aggregate_validation_evaluations(
    db_session,
    configuration_id=config.id,
    system="final_agent",
  )

  assert aggregate is None


@pytest.mark.asyncio
async def test_ignores_other_system_names(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.4",
    parent_version="v1.3",
    change_reason="System filter test.",
    generator_prompt_version="generator_v1.4",
    critic_prompt_version="critic_v1.3",
  )

  await record_configuration_evaluation(
    db_session,
    configuration_id=config.id,
    dataset_split="validation",
    system="baseline_a",
    repeat_number=1,
    precision=0.90,
    recall=0.90,
    f1=0.90,
    groundedness=0.90,
    abstention_accuracy=0.90,
    no_comment_accuracy=0.90,
    safety_policy_failures=0,
  )
  await db_session.commit()

  aggregate = await aggregate_validation_evaluations(
    db_session,
    configuration_id=config.id,
    system="final_agent",
  )

  assert aggregate is None


@pytest.mark.asyncio
async def test_missing_metric_makes_aggregate_incomplete(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.5",
    parent_version="v1.4",
    change_reason="Incomplete metric test.",
    generator_prompt_version="generator_v1.5",
    critic_prompt_version="critic_v1.4",
  )

  for repeat_number in range(1, MINIMUM_VALIDATION_REPEATS + 1):
    await record_configuration_evaluation(
      db_session,
      configuration_id=config.id,
      dataset_split="validation",
      system="final_agent",
      repeat_number=repeat_number,
      precision=None,
      recall=0.20,
      f1=0.13,
      groundedness=0.90,
      abstention_accuracy=0.70,
      no_comment_accuracy=1.0,
      safety_policy_failures=0,
    )
  await db_session.commit()

  aggregate = await aggregate_validation_evaluations(
    db_session,
    configuration_id=config.id,
    system="final_agent",
  )

  assert aggregate is None
