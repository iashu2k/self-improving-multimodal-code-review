import pytest

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.services.configurations import create_configuration_candidate, get_active_configuration
from app.services.promotion import (
  reject_configuration,
  rollback_active_configuration,
)


async def make_config(
  db_session,
  *,
  version: str,
  parent_version: str | None,
  status: str = ConfigurationStatus.DRAFT,
) -> ReviewConfiguration:
  config = await create_configuration_candidate(
    db_session,
    config_version=version,
    parent_version=parent_version,
    change_reason=f"Lifecycle test for {version}.",
    generator_prompt_version=f"generator_{version}",
    critic_prompt_version=f"critic_{version}",
  )
  config.status = status
  await db_session.commit()
  return config


@pytest.mark.asyncio
async def test_reject_configuration_records_reason(db_session) -> None:
  candidate = await make_config(
    db_session,
    version="v1.2",
    parent_version="v1.1",
    status=ConfigurationStatus.PENDING,
  )

  rejected = await reject_configuration(
    db_session,
    configuration_id=candidate.id,
    reason="Validation recall regressed beyond tolerance.",
  )
  await db_session.commit()

  assert rejected.status == ConfigurationStatus.REJECTED
  assert rejected.rejected_at is not None
  assert rejected.rejection_reason == "Validation recall regressed beyond tolerance."


@pytest.mark.asyncio
async def test_reject_configuration_rejects_blank_reason(db_session) -> None:
  candidate = await make_config(
    db_session,
    version="v1.3",
    parent_version="v1.2",
  )

  with pytest.raises(ValueError, match="rejection reason must not be blank"):
    await reject_configuration(
      db_session,
      configuration_id=candidate.id,
      reason="   ",
    )


@pytest.mark.asyncio
async def test_reject_configuration_does_not_reject_active_config(db_session) -> None:
  active = await make_config(
    db_session,
    version="v1.4",
    parent_version="v1.3",
    status=ConfigurationStatus.ACTIVE,
  )

  with pytest.raises(ValueError, match="active configuration cannot be rejected"):
    await reject_configuration(
      db_session,
      configuration_id=active.id,
      reason="Not allowed.",
    )


@pytest.mark.asyncio
async def test_rollback_restores_parent_configuration(db_session) -> None:
  parent = await make_config(
    db_session,
    version="v1.5",
    parent_version=None,
    status=ConfigurationStatus.ROLLED_BACK,
  )
  active = await make_config(
    db_session,
    version="v1.6",
    parent_version=parent.config_version,
    status=ConfigurationStatus.ACTIVE,
  )

  restored = await rollback_active_configuration(
    db_session,
    reason="Production feedback showed a false-positive regression.",
  )
  await db_session.commit()

  current_active = await get_active_configuration(db_session)
  previous = await db_session.get(ReviewConfiguration, active.id)

  assert restored.id == parent.id
  assert restored.status == ConfigurationStatus.ACTIVE
  assert current_active is not None
  assert current_active.id == parent.id
  assert previous.status == ConfigurationStatus.ROLLED_BACK
  assert previous.rollback_reason == "Production feedback showed a false-positive regression."


@pytest.mark.asyncio
async def test_rollback_requires_active_configuration(db_session) -> None:
  with pytest.raises(ValueError, match="No active configuration"):
    await rollback_active_configuration(
      db_session,
      reason="Nothing is active.",
    )


@pytest.mark.asyncio
async def test_rollback_requires_parent_configuration(db_session) -> None:
  await make_config(
    db_session,
    version="v1.7",
    parent_version=None,
    status=ConfigurationStatus.ACTIVE,
  )

  with pytest.raises(ValueError, match="has no parent configuration"):
    await rollback_active_configuration(
      db_session,
      reason="No parent exists.",
    )
