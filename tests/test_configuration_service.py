import pytest

from app.db.models.config import ConfigurationStatus
from app.services.configurations import (
  ConfigurationConflictError,
  create_configuration_candidate,
  get_active_configuration,
  record_configuration_evaluation,
)


@pytest.mark.asyncio
async def test_create_configuration_candidate_persists_draft(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Reduce false-positive missing-null-check comments.",
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
    router_rules={"force_security_review_paths": ["auth/"]},
    thresholds={"minimum_confidence": 0.78, "max_comments_per_pr": 5},
    model_versions={"review": "anthropic/claude-sonnet-4.5"},
    retrieval_config={"top_k": 8},
    repair_policy={"max_repairs": 2},
    created_by="manual",
  )
  await db_session.commit()

  assert config.config_version == "v1.2"
  assert config.parent_version == "v1.1"
  assert config.status == ConfigurationStatus.DRAFT
  assert config.thresholds["minimum_confidence"] == 0.78
  assert config.approved_at is None
  assert config.promoted_at is None


@pytest.mark.asyncio
async def test_create_configuration_candidate_rejects_duplicate_version(db_session) -> None:
  await create_configuration_candidate(
    db_session,
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="First candidate.",
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )
  await db_session.commit()

  with pytest.raises(ConfigurationConflictError, match="already exists"):
    await create_configuration_candidate(
      db_session,
      config_version="v1.2",
      parent_version="v1.1",
      change_reason="Duplicate candidate.",
      generator_prompt_version="generator_v1.2",
      critic_prompt_version="critic_v1.1",
    )


@pytest.mark.asyncio
async def test_record_configuration_evaluation_persists_metrics(db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.3",
    parent_version="v1.2",
    change_reason="Candidate evaluation test.",
    generator_prompt_version="generator_v1.3",
    critic_prompt_version="critic_v1.2",
  )

  evaluation = await record_configuration_evaluation(
    db_session,
    configuration_id=config.id,
    dataset_split="validation",
    system="final_agent",
    repeat_number=1,
    precision=0.12,
    recall=0.22,
    f1=0.16,
    groundedness=0.91,
    abstention_accuracy=0.75,
    no_comment_accuracy=1.0,
    safety_policy_failures=0,
    metrics={"run_label": "v8-val-v13-r1"},
  )
  await db_session.commit()

  assert evaluation.configuration_id == config.id
  assert evaluation.dataset_split == "validation"
  assert evaluation.system == "final_agent"
  assert evaluation.repeat_number == 1
  assert evaluation.safety_policy_failures == 0
  assert evaluation.metrics["run_label"] == "v8-val-v13-r1"


@pytest.mark.asyncio
async def test_get_active_configuration_returns_none_before_activation(db_session) -> None:
  await create_configuration_candidate(
    db_session,
    config_version="v1.4",
    parent_version="v1.3",
    change_reason="No active configuration yet.",
    generator_prompt_version="generator_v1.4",
    critic_prompt_version="critic_v1.3",
  )
  await db_session.commit()

  assert await get_active_configuration(db_session) is None
