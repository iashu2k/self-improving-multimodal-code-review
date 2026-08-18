import pytest

from app.db.models.config import (
  ConfigurationEvaluation,
  ConfigurationStatus,
  ReviewConfiguration,
)


@pytest.mark.asyncio
async def test_review_configuration_persists_versioned_candidate(db_session) -> None:
  config = ReviewConfiguration(
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Reduce false-positive missing-null-check comments.",
    status=ConfigurationStatus.PENDING,
    router_rules={"force_security_review_paths": ["auth/"]},
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
    thresholds={
      "minimum_confidence": 0.78,
      "max_comments_per_pr": 5,
    },
    model_versions={
      "review": "anthropic/claude-sonnet-4.5",
      "critic": "qwen/qwen3-coder-next",
    },
    retrieval_config={"top_k": 8, "rrf_k": 60},
    repair_policy={"max_repairs": 2},
    evaluation_summary={"development": {"f1": 0.14}},
    created_by="manual",
  )
  db_session.add(config)
  await db_session.commit()

  persisted = await db_session.get(ReviewConfiguration, config.id)

  assert persisted is not None
  assert persisted.config_version == "v1.2"
  assert persisted.parent_version == "v1.1"
  assert persisted.status == ConfigurationStatus.PENDING
  assert persisted.thresholds["minimum_confidence"] == 0.78
  assert persisted.repair_policy["max_repairs"] == 2
  assert persisted.created_at is not None


@pytest.mark.asyncio
async def test_configuration_evaluation_links_to_candidate(db_session) -> None:
  config = ReviewConfiguration(
    config_version="v1.3",
    parent_version="v1.2",
    change_reason="Test candidate evaluation persistence.",
    generator_prompt_version="generator_v1.3",
    critic_prompt_version="critic_v1.2",
  )
  db_session.add(config)
  await db_session.flush()

  evaluation = ConfigurationEvaluation(
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
  db_session.add(evaluation)
  await db_session.commit()

  persisted = await db_session.get(ConfigurationEvaluation, evaluation.id)

  assert persisted is not None
  assert persisted.configuration_id == config.id
  assert persisted.dataset_split == "validation"
  assert persisted.system == "final_agent"
  assert persisted.repeat_number == 1
  assert persisted.safety_policy_failures == 0
  assert persisted.metrics["run_label"] == "v8-val-v13-r1"
