from dataclasses import dataclass

from app.db.models.config import ReviewConfiguration
from app.diagnosis.report import DiagnosisReport


@dataclass(frozen=True)
class CandidateProposal:
  config_version: str
  parent_version: str
  change_reason: str
  router_rules: dict
  generator_prompt_version: str
  critic_prompt_version: str
  thresholds: dict
  model_versions: dict
  retrieval_config: dict
  repair_policy: dict


def propose_configuration_candidate(
  active: ReviewConfiguration,
  *,
  report: DiagnosisReport,
  new_version: str,
) -> CandidateProposal | None:
  if report.total_failures == 0:
    return None

  normalized_version = new_version.strip()
  if not normalized_version:
    raise ValueError("new_version must not be blank")
  if normalized_version == active.config_version:
    raise ValueError("new_version must differ from the active configuration")

  thresholds = dict(active.thresholds or {})
  retrieval_config = dict(active.retrieval_config or {})
  dominant_categories = [cluster.category for cluster in report.clusters]

  if "false_positive" in dominant_categories:
    current_confidence = float(thresholds.get("minimum_confidence", 0.75))
    thresholds["minimum_confidence"] = round(min(current_confidence + 0.03, 0.95), 2)

  if "duplicate" in dominant_categories:
    current_cap = int(thresholds.get("max_comments_per_pr", 5))
    thresholds["max_comments_per_pr"] = max(current_cap - 1, 1)

  if "missing_context" in dominant_categories:
    current_top_k = int(retrieval_config.get("top_k", 8))
    retrieval_config["top_k"] = min(current_top_k + 2, 16)

  category_summary = ", ".join(
    f"{cluster.category}×{cluster.count}" for cluster in report.clusters[:5]
  )
  change_reason = (
    f"Diagnosed failure clusters for {active.config_version}: "
    f"{category_summary}. Proposed conservative policy adjustment."
  )

  return CandidateProposal(
    config_version=normalized_version,
    parent_version=active.config_version,
    change_reason=change_reason,
    router_rules=dict(active.router_rules or {}),
    generator_prompt_version=active.generator_prompt_version,
    critic_prompt_version=active.critic_prompt_version,
    thresholds=thresholds,
    model_versions=dict(active.model_versions or {}),
    retrieval_config=retrieval_config,
    repair_policy=dict(active.repair_policy or {}),
  )
