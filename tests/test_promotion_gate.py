from dataclasses import replace

from app.services.promotion_gate import (
  EvaluationAggregate,
  PromotionGateInput,
  evaluate_promotion_gate,
)


def make_input(
  *,
  candidate: EvaluationAggregate | None = None,
  active: EvaluationAggregate | None = None,
  manual_approval: bool = True,
  safety_policy_failures: int = 0,
) -> PromotionGateInput:
  return PromotionGateInput(
    candidate=candidate
    or EvaluationAggregate(
      precision=0.14,
      recall=0.24,
      groundedness=0.92,
      no_comment_accuracy=1.0,
      safety_policy_failures=safety_policy_failures,
      validation_repeats=2,
    ),
    active=active
    or EvaluationAggregate(
      precision=0.13,
      recall=0.20,
      groundedness=0.90,
      no_comment_accuracy=1.0,
      safety_policy_failures=0,
      validation_repeats=2,
    ),
    manual_approval=manual_approval,
  )


def test_candidate_passes_when_all_conditions_hold() -> None:
  decision = evaluate_promotion_gate(make_input())

  assert decision.eligible is True
  assert decision.failed_conditions == []


def test_precision_decline_over_two_points_fails() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, precision=0.109)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is False
  assert "validation_precision_decline" in decision.failed_conditions


def test_precision_decline_of_exactly_two_points_passes() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, precision=0.11)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is True


def test_recall_decline_over_two_points_fails() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, recall=0.179)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is False
  assert "validation_recall_decline" in decision.failed_conditions


def test_groundedness_decline_fails() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, groundedness=0.899)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )
  assert decision.eligible is False
  assert "groundedness_decline" in decision.failed_conditions


def test_no_comment_accuracy_decline_fails() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, no_comment_accuracy=0.99)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is False
  assert "no_comment_accuracy_decline" in decision.failed_conditions


def test_any_safety_policy_failure_fails() -> None:
  decision = evaluate_promotion_gate(make_input(safety_policy_failures=1))

  assert decision.eligible is False
  assert "safety_policy_failures" in decision.failed_conditions


def test_manual_approval_is_required() -> None:
  decision = evaluate_promotion_gate(make_input(manual_approval=False))

  assert decision.eligible is False
  assert "manual_approval_missing" in decision.failed_conditions


def test_missing_validation_repeats_fail() -> None:
  gate_input = make_input()
  candidate = replace(gate_input.candidate, validation_repeats=1)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is False
  assert "insufficient_validation_repeats" in decision.failed_conditions


def test_multiple_failures_are_all_reported() -> None:
  gate_input = make_input(manual_approval=False, safety_policy_failures=2)
  candidate = replace(gate_input.candidate, groundedness=0.80)

  decision = evaluate_promotion_gate(
    PromotionGateInput(
      candidate=candidate,
      active=gate_input.active,
      manual_approval=gate_input.manual_approval,
    )
  )

  assert decision.eligible is False
  assert "groundedness_decline" in decision.failed_conditions
  assert "safety_policy_failures" in decision.failed_conditions
  assert "manual_approval_missing" in decision.failed_conditions
