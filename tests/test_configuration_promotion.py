from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.services.configurations import (
  create_configuration_candidate,
  get_active_configuration,
  record_configuration_evaluation,
)
from app.services.promotion import (
  approve_configuration,
  promote_configuration,
)


async def seed_active_configuration(db_session) -> ReviewConfiguration:
  active = await create_configuration_candidate(
    db_session,
    config_version="v1.1",
    parent_version=None,
    change_reason="Initial production configuration.",
    generator_prompt_version="generator_v1.1",
    critic_prompt_version="critic_v1.1",
  )
  active.status = ConfigurationStatus.ACTIVE
  active.approved_by = "human-reviewer"
  active.approved_at = datetime(2026, 8, 18, 18, 0, tzinfo=UTC)
  active.promoted_at = datetime(2026, 8, 18, 18, 1, tzinfo=UTC)

  for repeat_number, metrics in enumerate(
    (
      {
        "precision": 0.13,
        "recall": 0.20,
        "groundedness": 0.90,
        "no_comment_accuracy": 1.0,
      },
      {
        "precision": 0.13,
        "recall": 0.20,
        "groundedness": 0.90,
        "no_comment_accuracy": 1.0,
      },
    ),
    start=1,
  ):
    await record_configuration_evaluation(
      db_session,
      configuration_id=active.id,
      dataset_split="validation",
      system="final_agent",
      repeat_number=repeat_number,
      **metrics,
      safety_policy_failures=0,
    )

  await db_session.commit()
  return active


async def seed_candidate(db_session, active: ReviewConfiguration) -> ReviewConfiguration:
  candidate = await create_configuration_candidate(
    db_session,
    config_version="v1.2",
    parent_version=active.config_version,
    change_reason="Improve recall without reducing safety.",
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )

  for repeat_number, metrics in enumerate(
    (
      {
        "precision": 0.14,
        "recall": 0.23,
        "groundedness": 0.92,
        "no_comment_accuracy": 1.0,
      },
      {
        "precision": 0.14,
        "recall": 0.23,
        "groundedness": 0.92,
        "no_comment_accuracy": 1.0,
      },
    ),
    start=1,
  ):
    await record_configuration_evaluation(
      db_session,
      configuration_id=candidate.id,
      dataset_split="validation",
      system="final_agent",
      repeat_number=repeat_number,
      **metrics,
      safety_policy_failures=0,
    )

  await db_session.commit()
  return candidate


@pytest.mark.asyncio
async def test_approve_configuration_records_human_approval(db_session) -> None:
  active = await seed_active_configuration(db_session)
  candidate = await seed_candidate(db_session, active)

  approved = await approve_configuration(
    db_session,
    configuration_id=candidate.id,
    approved_by="senior-maintainer",
  )
  await db_session.commit()

  assert approved.status == ConfigurationStatus.PENDING
  assert approved.approved_by == "senior-maintainer"
  assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_promotion_activates_approved_passing_candidate(db_session) -> None:
  active = await seed_active_configuration(db_session)
  candidate = await seed_candidate(db_session, active)
  await approve_configuration(
    db_session,
    configuration_id=candidate.id,
    approved_by="senior-maintainer",
  )

  decision = await promote_configuration(
    db_session,
    configuration_id=candidate.id,
    system="final_agent",
  )
  await db_session.commit()

  current_active = await get_active_configuration(db_session)
  previous = await db_session.get(ReviewConfiguration, active.id)

  assert decision.eligible is True
  assert decision.failed_conditions == []
  assert current_active is not None
  assert current_active.id == candidate.id
  assert candidate.status == ConfigurationStatus.ACTIVE
  assert candidate.promoted_at is not None
  assert previous.status == ConfigurationStatus.ROLLED_BACK
  assert previous.rolled_back_at is not None


@pytest.mark.asyncio
async def test_promotion_rejects_candidate_without_manual_approval(db_session) -> None:
  active = await seed_active_configuration(db_session)
  candidate = await seed_candidate(db_session, active)

  decision = await promote_configuration(
    db_session,
    configuration_id=candidate.id,
    system="final_agent",
  )
  await db_session.commit()

  current_active = await get_active_configuration(db_session)
  persisted_candidate = await db_session.get(ReviewConfiguration, candidate.id)

  assert decision.eligible is False
  assert "manual_approval_missing" in decision.failed_conditions
  assert current_active is not None
  assert current_active.id == active.id
  assert persisted_candidate.status == ConfigurationStatus.DRAFT
  assert persisted_candidate.promoted_at is None


@pytest.mark.asyncio
async def test_promotion_rejects_candidate_with_metric_regression(db_session) -> None:
  active = await seed_active_configuration(db_session)
  candidate = await seed_candidate(db_session, active)

  rows = (
    await db_session.scalars(
      select(ReviewConfiguration).where(ReviewConfiguration.id == candidate.id)
    )
  ).all()
  assert rows[0].id == candidate.id

  await approve_configuration(
    db_session,
    configuration_id=candidate.id,
    approved_by="senior-maintainer",
  )

  candidate_evaluations = await db_session.execute(
    select(ReviewConfiguration).where(ReviewConfiguration.id == candidate.id)
  )
  assert candidate_evaluations is not None

  # Replace candidate validation metrics with an unsafe groundedness regression.
  from app.db.models.config import ConfigurationEvaluation

  evaluations = (
    await db_session.scalars(
      select(ConfigurationEvaluation).where(
        ConfigurationEvaluation.configuration_id == candidate.id
      )
    )
  ).all()
  for evaluation in evaluations:
    evaluation.groundedness = 0.50
  await db_session.commit()

  decision = await promote_configuration(
    db_session,
    configuration_id=candidate.id,
    system="final_agent",
  )
  await db_session.commit()

  current_active = await get_active_configuration(db_session)
  persisted_candidate = await db_session.get(ReviewConfiguration, candidate.id)

  assert decision.eligible is False
  assert "groundedness_decline" in decision.failed_conditions
  assert current_active is not None
  assert current_active.id == active.id
  assert persisted_candidate.status == ConfigurationStatus.PENDING
  assert persisted_candidate.promoted_at is None


@pytest.mark.asyncio
async def test_promotion_requires_current_active_configuration(db_session) -> None:
  candidate = await create_configuration_candidate(
    db_session,
    config_version="v2.0",
    parent_version=None,
    change_reason="Cannot promote without active baseline.",
    generator_prompt_version="generator_v2.0",
    critic_prompt_version="critic_v2.0",
  )
  await approve_configuration(
    db_session,
    configuration_id=candidate.id,
    approved_by="senior-maintainer",
  )
  await db_session.commit()

  with pytest.raises(ValueError, match="No active configuration"):
    await promote_configuration(
      db_session,
      configuration_id=candidate.id,
      system="final_agent",
    )
