"""Layered matcher: structure first, semantics second.

Layers 1-3 are deterministic and cheap; layer 4 (LLM judge) only runs on
pairs that survive them. The judge's rationale is stored on every MatchRecord
so a human can audit a 20% sample later.

Field contracts (verified):
  GoldComment: file_path, line, category, severity, issue_summary,
               evidence_requirement, must_not_claim, rationale
  ReviewComment: file_path, line, category, severity, title, body, evidence
"""

from collections.abc import Sequence

from app.agents.schemas import ReviewComment, Severity
from app.evals.schemas import (
  CATEGORY_EQUIVALENTS,
  LINE_TOLERANCE,
  SEVERITY_LADDER,
  CandidatePair,
  JudgeDecision,
  JudgeVerdict,
  MatchRecord,
)


def deterministic_candidates(
  gold_comments: Sequence[object],
  generated: Sequence[ReviewComment],
  line_tolerance: int = LINE_TOLERANCE,
) -> list[CandidatePair]:
  """Layers 1-3: path equality, ±line window, category equivalence."""
  pairs: list[CandidatePair] = []
  for gi, gold in enumerate(gold_comments):
    accepted = CATEGORY_EQUIVALENTS.get(gold.category, frozenset({gold.category}))
    for pi, pred in enumerate(generated):
      if pred.file_path != gold.file_path:
        continue
      delta = abs(pred.line - gold.line)
      if delta > line_tolerance:
        continue
      if pred.category not in accepted:
        continue
      pairs.append(CandidatePair(gold_index=gi, generated_index=pi, line_delta=delta))
  return pairs


def resolve_matches(
  example_id: str,
  n_gold: int,
  n_generated: int,
  pairs: Sequence[CandidatePair],
  decisions: Sequence[JudgeDecision],
) -> list[MatchRecord]:
  """Greedily bind gold issues to generated comments using judge verdicts.

  One generated comment can satisfy at most one gold issue and vice versa;
  surplus comments on an already-matched gold issue become FPs (duplicates).
  """
  by_key = {(d.gold_index, d.generated_index): d for d in decisions}
  equivalent = [
    (p.gold_index, p.generated_index)
    for p in sorted(pairs, key=lambda p: p.line_delta)
    if by_key.get((p.gold_index, p.generated_index))
    and by_key[(p.gold_index, p.generated_index)].verdict == JudgeVerdict.EQUIVALENT
  ]
  matched_gold: dict[int, int] = {}
  used_pred: set[int] = set()
  for gi, pi in equivalent:
    if gi in matched_gold or pi in used_pred:
      continue
    matched_gold[gi] = pi
    used_pred.add(pi)

  records: list[MatchRecord] = []
  for gi in range(n_gold):
    pi = matched_gold.get(gi)
    decision = by_key.get((gi, pi)) if pi is not None else None
    records.append(
      MatchRecord(
        example_id=example_id,
        gold_index=gi,
        generated_index=pi,
        verdict=decision.verdict if decision else None,
        matched=pi is not None,
        judge_rationale=decision.rationale if decision else None,
      )
    )
  return records


def severity_agrees(predicted: Severity, gold: Severity) -> bool:
  if predicted == gold:
    return True
  try:
    return abs(SEVERITY_LADDER.index(predicted) - SEVERITY_LADDER.index(gold)) <= 1
  except ValueError:
    return False
