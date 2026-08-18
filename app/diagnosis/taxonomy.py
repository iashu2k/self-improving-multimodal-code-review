from dataclasses import dataclass
from enum import StrEnum

from app.db.models.feedback import FeedbackLabel


class FailureCategory(StrEnum):
  FALSE_POSITIVE = "false_positive"
  FALSE_NEGATIVE = "false_negative"
  GROUNDING_FAILURE = "grounding_failure"
  DUPLICATE = "duplicate"
  WRONG_SEVERITY = "wrong_severity"
  NOT_ACTIONABLE = "not_actionable"
  MISSING_CONTEXT = "missing_context"
  PLACEMENT_FAILURE = "placement_failure"
  RETRIEVAL_MISS = "retrieval_miss"
  REPAIR_FAILURE = "repair_failure"


@dataclass(frozen=True)
class FailureAttribution:
  category: FailureCategory
  agent_node: str


def classify_feedback_failure(label: str) -> FailureAttribution | None:
  normalized_label = FeedbackLabel(label)

  if normalized_label == FeedbackLabel.HELPFUL:
    return None
  if normalized_label == FeedbackLabel.FALSE_POSITIVE:
    return FailureAttribution(
      category=FailureCategory.FALSE_POSITIVE,
      agent_node="review_generator",
    )
  if normalized_label == FeedbackLabel.WRONG_SEVERITY:
    return FailureAttribution(
      category=FailureCategory.WRONG_SEVERITY,
      agent_node="review_generator",
    )
  if normalized_label == FeedbackLabel.NOT_ACTIONABLE:
    return FailureAttribution(
      category=FailureCategory.NOT_ACTIONABLE,
      agent_node="review_generator",
    )
  if normalized_label == FeedbackLabel.MISSING_CONTEXT:
    return FailureAttribution(
      category=FailureCategory.MISSING_CONTEXT,
      agent_node="rag_retriever",
    )
  if normalized_label == FeedbackLabel.DUPLICATE:
    return FailureAttribution(
      category=FailureCategory.DUPLICATE,
      agent_node="critic_qa",
    )

  return None


def classify_eval_match_failure(
  *,
  matched: bool,
  generated_index: int | None,
  judge_rationale: str | None,
) -> FailureAttribution | None:
  if matched:
    return None

  if generated_index is None:
    return FailureAttribution(
      category=FailureCategory.FALSE_NEGATIVE,
      agent_node="review_generator",
    )

  rationale = (judge_rationale or "").lower()
  if any(
    token in rationale
    for token in (
      "not grounded",
      "ungrounded",
      "hallucinat",
      "unsupported claim",
      "not supported",
    )
  ):
    return FailureAttribution(
      category=FailureCategory.GROUNDING_FAILURE,
      agent_node="review_generator",
    )

  if "duplicate" in rationale:
    return FailureAttribution(
      category=FailureCategory.DUPLICATE,
      agent_node="critic_qa",
    )

  if "line" in rationale and any(
    token in rationale for token in ("invalid", "wrong", "incorrect", "outside")
  ):
    return FailureAttribution(
      category=FailureCategory.PLACEMENT_FAILURE,
      agent_node="review_generator",
    )

  return FailureAttribution(
    category=FailureCategory.FALSE_POSITIVE,
    agent_node="review_generator",
  )
