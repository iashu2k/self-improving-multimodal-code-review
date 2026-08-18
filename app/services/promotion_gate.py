from dataclasses import dataclass

PRECISION_TOLERANCE = 0.02
RECALL_TOLERANCE = 0.02
MINIMUM_VALIDATION_REPEATS = 2


@dataclass(frozen=True)
class EvaluationAggregate:
  precision: float
  recall: float
  groundedness: float
  no_comment_accuracy: float
  safety_policy_failures: int
  validation_repeats: int


@dataclass(frozen=True)
class PromotionGateInput:
  candidate: EvaluationAggregate
  active: EvaluationAggregate
  manual_approval: bool


@dataclass(frozen=True)
class PromotionGateDecision:
  eligible: bool
  failed_conditions: list[str]


def evaluate_promotion_gate(gate_input: PromotionGateInput) -> PromotionGateDecision:
  failed_conditions: list[str] = []

  candidate = gate_input.candidate
  active = gate_input.active

  if candidate.validation_repeats < MINIMUM_VALIDATION_REPEATS:
    failed_conditions.append("insufficient_validation_repeats")

  if candidate.precision < active.precision - PRECISION_TOLERANCE:
    failed_conditions.append("validation_precision_decline")

  if candidate.recall < active.recall - RECALL_TOLERANCE:
    failed_conditions.append("validation_recall_decline")

  if candidate.groundedness < active.groundedness:
    failed_conditions.append("groundedness_decline")

  if candidate.no_comment_accuracy < active.no_comment_accuracy:
    failed_conditions.append("no_comment_accuracy_decline")

  if candidate.safety_policy_failures != 0:
    failed_conditions.append("safety_policy_failures")

  if not gate_input.manual_approval:
    failed_conditions.append("manual_approval_missing")

  return PromotionGateDecision(
    eligible=not failed_conditions,
    failed_conditions=failed_conditions,
  )
