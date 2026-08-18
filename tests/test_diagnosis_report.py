import uuid
from datetime import UTC, datetime

import pytest

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.db.models.eval import EvalExampleResult, EvalMatch, EvalRun
from app.db.models.feedback import (
  AttributionConfidence,
  CommentFeedback,
  FeedbackActorType,
  FeedbackLabel,
  FeedbackSource,
  FeedbackTargetType,
)
from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
from app.diagnosis.report import build_diagnosis_report


async def seed_config(db_session, version: str) -> ReviewConfiguration:
  config = ReviewConfiguration(
    config_version=version,
    parent_version=None,
    change_reason=f"Seed {version}.",
    status=ConfigurationStatus.ACTIVE,
    generator_prompt_version=f"generator_{version}",
    critic_prompt_version=f"critic_{version}",
  )
  db_session.add(config)
  await db_session.flush()
  return config


async def seed_review_feedback(db_session, *, config_version: str) -> None:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=99,
    head_sha="a" * 40,
    config_version=config_version,
    status=RunStatus.PUBLISHED,
  )
  db_session.add(run)
  await db_session.flush()

  comment = StoredReviewComment(
    run_id=run.id,
    file_path="src/client.py",
    line=24,
    severity="high",
    category="bug_risk",
    title="Missing null guard",
    body="The client can receive a missing token.",
    suggested_fix="Return before invoking the client.",
    confidence=0.91,
    status=CommentStatus.POSTED,
  )
  db_session.add(comment)
  await db_session.flush()

  db_session.add(
    CommentFeedback(
      run_id=run.id,
      stored_comment_id=comment.id,
      target_type=FeedbackTargetType.COMMENT,
      label=FeedbackLabel.FALSE_POSITIVE,
      free_text="Caller already validates this.",
      actor_type=FeedbackActorType.MAINTAINER,
      actor_login_hash="a" * 64,
      source=FeedbackSource.GITHUB_COMMENT_COMMAND,
      source_event_id="github-delivery-001",
      source_artifact_id="700",
      attribution_confidence=AttributionConfidence.EXACT_MARKER,
      created_at=datetime(2026, 8, 18, 19, 0, tzinfo=UTC),
    )
  )


async def seed_eval_failure(db_session, *, config_version: str) -> None:
  eval_run = EvalRun(
    id=uuid.uuid4(),
    config_version=config_version,
    dataset_split="validation",
    systems=["final_agent"],
    status="completed",
    started_at=datetime(2026, 8, 18, 19, 10, tzinfo=UTC),
  )
  db_session.add(eval_run)
  await db_session.flush()

  example = EvalExampleResult(
    id=uuid.uuid4(),
    run_id=eval_run.id,
    example_id="example-001",
    system="final_agent",
    attempt=1,
    tp=0,
    fp=1,
    fn=0,
  )
  db_session.add(example)
  await db_session.flush()

  db_session.add(
    EvalMatch(
      id=uuid.uuid4(),
      run_id=eval_run.id,
      example_result_id=example.id,
      example_id="example-001",
      gold_index=0,
      generated_index=0,
      verdict="not_equivalent",
      matched=False,
      judge_rationale="The generated comment is not grounded in the shown diff hunk.",
    )
  )


@pytest.mark.asyncio
async def test_diagnosis_report_groups_feedback_and_eval_failures(db_session) -> None:
  config = await seed_config(db_session, "v1.2")
  await seed_review_feedback(db_session, config_version="v1.2")
  await seed_eval_failure(db_session, config_version="v1.2")
  await db_session.commit()

  report = await build_diagnosis_report(
    db_session,
    configuration_id=config.id,
  )

  assert report.config_version == "v1.2"
  assert report.total_failures == 2
  assert len(report.clusters) == 2

  clusters = {(cluster.category, cluster.agent_node): cluster for cluster in report.clusters}

  false_positive = clusters[("false_positive", "review_generator")]
  assert false_positive.count == 1
  assert false_positive.sources == ["github_comment_command"]
  assert false_positive.examples[0].free_text == "Caller already validates this."

  grounding = clusters[("grounding_failure", "review_generator")]
  assert grounding.count == 1
  assert grounding.sources == ["eval_match"]
  assert grounding.examples[0].example_id == "example-001"


@pytest.mark.asyncio
async def test_diagnosis_report_excludes_helpful_feedback(db_session) -> None:
  config = await seed_config(db_session, "v1.3")
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=100,
    head_sha="b" * 40,
    config_version="v1.3",
    status=RunStatus.PUBLISHED,
  )
  db_session.add(run)
  await db_session.flush()

  db_session.add(
    CommentFeedback(
      run_id=run.id,
      stored_comment_id=None,
      target_type=FeedbackTargetType.REVIEW_SUMMARY,
      label=FeedbackLabel.HELPFUL,
      free_text=None,
      actor_type=FeedbackActorType.DEVELOPER,
      actor_login_hash=None,
      source=FeedbackSource.GITHUB_COMMENT_COMMAND,
      source_event_id="github-delivery-helpful",
      source_artifact_id="701",
      attribution_confidence=AttributionConfidence.EXACT_MARKER,
      created_at=datetime(2026, 8, 18, 19, 20, tzinfo=UTC),
    )
  )
  await db_session.commit()

  report = await build_diagnosis_report(
    db_session,
    configuration_id=config.id,
  )

  assert report.total_failures == 0
  assert report.clusters == []


@pytest.mark.asyncio
async def test_diagnosis_report_rejects_unknown_configuration(db_session) -> None:
  import uuid

  with pytest.raises(ValueError, match="configuration does not exist"):
    await build_diagnosis_report(
      db_session,
      configuration_id=uuid.uuid4(),
    )
