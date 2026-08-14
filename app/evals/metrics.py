"""Metric computation over judged matches. Pure functions, no I/O.

GoldComment.severity is required by the schema, so severity agreement is
always computable on matched pairs.
"""

from collections.abc import Sequence

from app.agents.schemas import ReviewComment
from app.evals.matcher import severity_agrees
from app.evals.schemas import (
  AggregateMetrics,
  ExampleMetrics,
  MatchRecord,
  RoutingConfusion,
  SystemName,
)


def score_example(
  example_id: str,
  system: SystemName,
  gold_comments: Sequence[object],
  generated: Sequence[ReviewComment],
  matches: Sequence[MatchRecord],
  grounded_flags: Sequence[bool],
  line_valid_flags: Sequence[bool],
  attempt: int = 1,
) -> ExampleMetrics:
  matched_gold = {m.gold_index for m in matches if m.matched}
  matched_pred = {m.generated_index for m in matches if m.matched and m.generated_index is not None}

  tp = len(matched_gold)
  fn = len(gold_comments) - tp
  fp = len(generated) - len(matched_pred)

  severity_hits = 0
  for m in matches:
    if not m.matched or m.generated_index is None:
      continue
    if severity_agrees(generated[m.generated_index].severity, gold_comments[m.gold_index].severity):
      severity_hits += 1

  return ExampleMetrics(
    example_id=example_id,
    system=system,
    attempt=attempt,
    tp=tp,
    fp=fp,
    fn=fn,
    grounded_comments=sum(1 for g in grounded_flags if g),
    line_valid_comments=sum(1 for v in line_valid_flags if v),
    severity_agreements=severity_hits,
    matched_with_severity=tp,
    total_comments=len(generated),
    expected_empty=len(gold_comments) == 0,
    predicted_empty=len(generated) == 0,
  )


def _rate(num: int, den: int) -> float | None:
  return num / den if den else None


def aggregate(
  system: SystemName,
  split: str,
  per_example: Sequence[ExampleMetrics],
  pass_attempts: dict[str, list[bool]] | None = None,
  total_cost_usd: float = 0.0,
) -> AggregateMetrics:
  tp = sum(m.tp for m in per_example)
  fp = sum(m.fp for m in per_example)
  fn = sum(m.fn for m in per_example)
  comments = sum(m.total_comments for m in per_example)
  grounded = sum(m.grounded_comments for m in per_example)
  line_valid = sum(m.line_valid_comments for m in per_example)
  sev_hits = sum(m.severity_agreements for m in per_example)
  sev_base = sum(m.matched_with_severity for m in per_example)

  negatives = [m for m in per_example if m.expected_empty]
  correct_abstentions = sum(1 for m in negatives if m.predicted_empty)

  p = _rate(tp, tp + fp)
  r = _rate(tp, tp + fn)

  pass_at_1 = pass_at_2 = None
  if pass_attempts:
    evaluated = len(pass_attempts)
    if evaluated:
      pass_at_1 = sum(1 for v in pass_attempts.values() if v and v[0]) / evaluated
      pass_at_2 = sum(1 for v in pass_attempts.values() if any(v[:2])) / evaluated

  return AggregateMetrics(
    system=system,
    split=split,
    examples=len(per_example),
    tp=tp,
    fp=fp,
    fn=fn,
    precision=p,
    recall=r,
    f1=(2 * p * r / (p + r)) if p is not None and r is not None and (p + r) else None,
    groundedness_rate=_rate(grounded, comments),
    line_validity_rate=_rate(line_valid, comments),
    severity_agreement_rate=_rate(sev_hits, sev_base),
    negative_examples=len(negatives),
    correct_abstentions=correct_abstentions,
    no_comment_accuracy=_rate(correct_abstentions, len(negatives)),
    pass_at_1=pass_at_1,
    pass_at_2=pass_at_2,
    total_cost_usd=total_cost_usd,
  )


def routing_confusion(per_example: Sequence[ExampleMetrics]) -> RoutingConfusion:
  matrix = RoutingConfusion()
  for m in per_example:
    if m.expected_empty and m.predicted_empty:
      matrix.true_abstain += 1
    elif m.expected_empty and not m.predicted_empty:
      matrix.false_comment += 1
    elif not m.expected_empty and m.predicted_empty:
      matrix.false_abstain += 1
    else:
      matrix.true_comment += 1
  return matrix
