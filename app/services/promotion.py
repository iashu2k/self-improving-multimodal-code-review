import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.observability import root_trace
from app.services.configurations import get_active_configuration
from app.services.promotion_data import aggregate_validation_evaluations
from app.services.promotion_gate import (
  PromotionGateDecision,
  PromotionGateInput,
  evaluate_promotion_gate,
)


async def approve_configuration(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  approved_by: str,
) -> ReviewConfiguration:
  normalized_approver = approved_by.strip()
  if not normalized_approver:
    raise ValueError("approved_by must not be blank")

  config = await session.get(ReviewConfiguration, configuration_id)
  if config is None:
    raise ValueError("configuration does not exist")

  if config.status not in {
    ConfigurationStatus.DRAFT,
    ConfigurationStatus.PENDING,
  }:
    raise ValueError("only draft or pending configurations can be approved")

  config.status = ConfigurationStatus.PENDING
  config.approved_by = normalized_approver
  config.approved_at = datetime.now(UTC)

  await session.flush()
  return config


async def promote_configuration(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  system: str,
) -> PromotionGateDecision:
  candidate = await session.get(ReviewConfiguration, configuration_id)
  if candidate is None:
    raise ValueError("configuration does not exist")

  # Phase 9: one trace per candidate collects every promotion attempt.
  # Guard failures (the route maps them to 4xx) surface as ERROR spans;
  # gate decisions carry eligible and failed_conditions as trace output.
  async with root_trace(
    "promotion_decision",
    trace_seed=f"promotion-{candidate.id}",
    metadata={
      "configuration_id": str(candidate.id),
      "config_version": candidate.config_version,
      "system": system,
    },
    tags=["phase8", "promotion"],
  ) as trace:
    if candidate.status not in {
      ConfigurationStatus.DRAFT,
      ConfigurationStatus.PENDING,
    }:
      raise ValueError("only draft or pending configurations can be promoted")

    active = await get_active_configuration(session)
    if active is None:
      raise ValueError("No active configuration is available for comparison")

    candidate_aggregate = await aggregate_validation_evaluations(
      session,
      configuration_id=candidate.id,
      system=system,
    )
    active_aggregate = await aggregate_validation_evaluations(
      session,
      configuration_id=active.id,
      system=system,
    )

    if candidate_aggregate is None:
      raise ValueError("candidate has no complete validation evaluation aggregate")
    if active_aggregate is None:
      raise ValueError("active configuration has no complete validation evaluation aggregate")

    manual_approval = bool(candidate.approved_by and candidate.approved_at)

    decision = evaluate_promotion_gate(
      PromotionGateInput(
        candidate=candidate_aggregate,
        active=active_aggregate,
        manual_approval=manual_approval,
      )
    )

    if not decision.eligible:
      candidate.evaluation_summary = {
        **candidate.evaluation_summary,
        "promotion_gate": {
          "eligible": False,
          "failed_conditions": decision.failed_conditions,
        },
      }
      await session.flush()
      trace.update(
        output={
          "eligible": False,
          "failed_conditions": decision.failed_conditions,
          "candidate": candidate.config_version,
          "active": active.config_version,
        }
      )
      return decision

    now = datetime.now(UTC)
    active.status = ConfigurationStatus.ROLLED_BACK
    active.rolled_back_at = now
    active.rollback_reason = f"Superseded by {candidate.config_version}"

    candidate.status = ConfigurationStatus.ACTIVE
    candidate.promoted_at = now
    candidate.evaluated_at = now
    candidate.evaluation_summary = {
      **candidate.evaluation_summary,
      "promotion_gate": {
        "eligible": True,
        "failed_conditions": [],
        "promoted_from": active.config_version,
      },
    }

    await session.flush()
    trace.update(
      output={
        "eligible": True,
        "failed_conditions": [],
        "promoted": candidate.config_version,
        "rolled_back": active.config_version,
      }
    )
    return decision


async def reject_configuration(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  reason: str,
) -> ReviewConfiguration:
  normalized_reason = reason.strip()
  if not normalized_reason:
    raise ValueError("rejection reason must not be blank")

  config = await session.get(ReviewConfiguration, configuration_id)
  if config is None:
    raise ValueError("configuration does not exist")
  if config.status == ConfigurationStatus.ACTIVE:
    raise ValueError("active configuration cannot be rejected")

  config.status = ConfigurationStatus.REJECTED
  config.rejected_at = datetime.now(UTC)
  config.rejection_reason = normalized_reason

  await session.flush()
  return config


async def rollback_active_configuration(
  session: AsyncSession,
  *,
  reason: str,
) -> ReviewConfiguration:
  normalized_reason = reason.strip()
  if not normalized_reason:
    raise ValueError("rollback reason must not be blank")

  active = await get_active_configuration(session)
  if active is None:
    raise ValueError("No active configuration is available to roll back")

  # Phase 9: rollback is a safety reversal, not a promotion, and gets its
  # own trace family. Repeated rollbacks of one config merge via the seed.
  async with root_trace(
    "configuration_rollback",
    trace_seed=f"rollback-{active.id}",
    metadata={
      "active_configuration_id": str(active.id),
      "active_version": active.config_version,
    },
    tags=["phase8", "rollback"],
  ) as trace:
    if not active.parent_version:
      raise ValueError("active configuration has no parent configuration")

    parent = await session.scalar(
      select(ReviewConfiguration).where(ReviewConfiguration.config_version == active.parent_version)
    )
    if parent is None:
      raise ValueError("parent configuration does not exist")
    if parent.status == ConfigurationStatus.ACTIVE:
      raise ValueError("parent configuration is already active")

    now = datetime.now(UTC)
    active.status = ConfigurationStatus.ROLLED_BACK
    active.rolled_back_at = now
    active.rollback_reason = normalized_reason

    parent.status = ConfigurationStatus.ACTIVE
    parent.promoted_at = now
    parent.rolled_back_at = None
    parent.rollback_reason = None

    await session.flush()
    trace.update(
      output={
        "rolled_back": active.config_version,
        "restored": parent.config_version,
        "reason": normalized_reason,
      }
    )
    return parent
