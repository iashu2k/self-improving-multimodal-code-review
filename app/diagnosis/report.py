import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.config import ReviewConfiguration
from app.db.models.eval import EvalMatch, EvalRun
from app.db.models.feedback import CommentFeedback
from app.db.models.review import ReviewRun, StoredReviewComment
from app.diagnosis.taxonomy import classify_eval_match_failure, classify_feedback_failure
from app.observability import root_trace


@dataclass(frozen=True)
class DiagnosisExample:
  source: str
  category: str
  agent_node: str
  run_id: int | None = None
  stored_comment_id: int | None = None
  example_id: str | None = None
  free_text: str | None = None
  judge_rationale: str | None = None


@dataclass
class DiagnosisCluster:
  category: str
  agent_node: str
  count: int = 0
  sources: list[str] = field(default_factory=list)
  examples: list[DiagnosisExample] = field(default_factory=list)


@dataclass(frozen=True)
class DiagnosisReport:
  configuration_id: str
  config_version: str
  total_failures: int
  clusters: list[DiagnosisCluster]


def _cluster_key(category: str, agent_node: str) -> tuple[str, str]:
  return category, agent_node


async def build_diagnosis_report(
  session: AsyncSession,
  *,
  configuration_id: uuid.UUID,
  max_examples_per_cluster: int = 5,
) -> DiagnosisReport:
  config = await session.get(ReviewConfiguration, configuration_id)
  if config is None:
    raise ValueError("configuration does not exist")

  # Phase 9: trace report generation. Repeated reports for one config
  # merge into a single trace seeded by the configuration ID, so the
  # evolution of the failure mix stays in one place.
  async with root_trace(
    "diagnosis_report",
    trace_seed=f"diagnosis-{configuration_id}",
    metadata={
      "configuration_id": str(configuration_id),
      "config_version": config.config_version,
    },
    tags=["phase8", "diagnosis"],
  ) as trace:
    clusters: dict[tuple[str, str], DiagnosisCluster] = {}

    def add_example(
      *,
      category: str,
      agent_node: str,
      source: str,
      example: DiagnosisExample,
    ) -> None:
      key = _cluster_key(category, agent_node)
      cluster = clusters.setdefault(
        key,
        DiagnosisCluster(category=category, agent_node=agent_node),
      )
      cluster.count += 1
      cluster.sources.append(source)
      if len(cluster.examples) < max_examples_per_cluster:
        cluster.examples.append(example)

    feedback_rows = (
      await session.execute(
        select(CommentFeedback, ReviewRun, StoredReviewComment)
        .join(ReviewRun, ReviewRun.id == CommentFeedback.run_id)
        .outerjoin(
          StoredReviewComment,
          StoredReviewComment.id == CommentFeedback.stored_comment_id,
        )
        .where(ReviewRun.config_version == config.config_version)
        .order_by(CommentFeedback.created_at)
      )
    ).all()

    for feedback, run, stored_comment in feedback_rows:
      attribution = classify_feedback_failure(feedback.label)
      if attribution is None:
        continue

      add_example(
        category=attribution.category,
        agent_node=attribution.agent_node,
        source=feedback.source,
        example=DiagnosisExample(
          source=feedback.source,
          category=attribution.category,
          agent_node=attribution.agent_node,
          run_id=run.id,
          stored_comment_id=stored_comment.id if stored_comment else None,
          free_text=feedback.free_text,
        ),
      )

    eval_rows = (
      await session.execute(
        select(EvalMatch, EvalRun)
        .join(EvalRun, EvalRun.id == EvalMatch.run_id)
        .where(EvalRun.config_version == config.config_version)
        .order_by(EvalMatch.example_id)
      )
    ).all()

    for match, _eval_run in eval_rows:
      attribution = classify_eval_match_failure(
        matched=match.matched,
        generated_index=match.generated_index,
        judge_rationale=match.judge_rationale,
      )
      if attribution is None:
        continue

      add_example(
        category=attribution.category,
        agent_node=attribution.agent_node,
        source="eval_match",
        example=DiagnosisExample(
          source="eval_match",
          category=attribution.category,
          agent_node=attribution.agent_node,
          example_id=match.example_id,
          judge_rationale=match.judge_rationale,
        ),
      )

    ordered_clusters = sorted(
      clusters.values(),
      key=lambda cluster: (-cluster.count, cluster.category, cluster.agent_node),
    )

    for cluster in ordered_clusters:
      cluster.sources = sorted(set(cluster.sources))

    report = DiagnosisReport(
      configuration_id=str(config.id),
      config_version=config.config_version,
      total_failures=sum(cluster.count for cluster in ordered_clusters),
      clusters=ordered_clusters,
    )
    trace.update(
      output={
        "total_failures": report.total_failures,
        "cluster_count": len(report.clusters),
        "top_clusters": [
          {"category": c.category, "agent_node": c.agent_node, "count": c.count}
          for c in report.clusters[:5]
        ],
      }
    )
    return report
