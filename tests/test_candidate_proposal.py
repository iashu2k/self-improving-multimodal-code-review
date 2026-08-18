from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.diagnosis.proposal import propose_configuration_candidate
from app.diagnosis.report import DiagnosisCluster, DiagnosisReport


def make_config() -> ReviewConfiguration:
  return ReviewConfiguration(
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Current production configuration.",
    status=ConfigurationStatus.ACTIVE,
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
    thresholds={
      "minimum_confidence": 0.75,
      "max_comments_per_pr": 6,
    },
    model_versions={"review": "anthropic/claude-sonnet-4.5"},
    retrieval_config={"top_k": 8},
    repair_policy={"max_repairs": 2},
  )


def make_report(clusters: list[DiagnosisCluster]) -> DiagnosisReport:
  return DiagnosisReport(
    configuration_id="config-id",
    config_version="v1.2",
    total_failures=sum(cluster.count for cluster in clusters),
    clusters=clusters,
  )


def test_false_positive_cluster_proposes_higher_confidence_threshold() -> None:
  report = make_report(
    [
      DiagnosisCluster(
        category="false_positive",
        agent_node="review_generator",
        count=4,
        sources=["github_comment_command"],
      )
    ]
  )

  proposal = propose_configuration_candidate(
    make_config(),
    report=report,
    new_version="v1.3",
  )

  assert proposal.config_version == "v1.3"
  assert proposal.parent_version == "v1.2"
  assert "false_positive" in proposal.change_reason
  assert proposal.thresholds["minimum_confidence"] == 0.78
  assert proposal.thresholds["max_comments_per_pr"] == 6
  assert proposal.generator_prompt_version == "generator_v1.2"
  assert proposal.critic_prompt_version == "critic_v1.1"


def test_missing_context_cluster_proposes_larger_retrieval_window() -> None:
  report = make_report(
    [
      DiagnosisCluster(
        category="missing_context",
        agent_node="rag_retriever",
        count=3,
        sources=["github_comment_command"],
      )
    ]
  )

  proposal = propose_configuration_candidate(
    make_config(),
    report=report,
    new_version="v1.3",
  )

  assert "missing_context" in proposal.change_reason
  assert proposal.retrieval_config["top_k"] == 10
  assert proposal.thresholds["minimum_confidence"] == 0.75


def test_duplicate_cluster_proposes_lower_comment_cap() -> None:
  report = make_report(
    [
      DiagnosisCluster(
        category="duplicate",
        agent_node="critic_qa",
        count=2,
        sources=["github_comment_command"],
      )
    ]
  )

  proposal = propose_configuration_candidate(
    make_config(),
    report=report,
    new_version="v1.3",
  )

  assert "duplicate" in proposal.change_reason
  assert proposal.thresholds["max_comments_per_pr"] == 5


def test_no_failures_returns_none() -> None:
  proposal = propose_configuration_candidate(
    make_config(),
    report=make_report([]),
    new_version="v1.3",
  )

  assert proposal is None


def test_unknown_cluster_does_not_change_policy() -> None:
  report = make_report(
    [
      DiagnosisCluster(
        category="placement_failure",
        agent_node="review_generator",
        count=2,
        sources=["eval_match"],
      )
    ]
  )

  proposal = propose_configuration_candidate(
    make_config(),
    report=report,
    new_version="v1.3",
  )

  assert proposal is not None
  assert "placement_failure" in proposal.change_reason
  assert proposal.thresholds["minimum_confidence"] == 0.75
  assert proposal.thresholds["max_comments_per_pr"] == 6
  assert proposal.retrieval_config["top_k"] == 8
