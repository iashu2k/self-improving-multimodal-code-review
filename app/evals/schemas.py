"""Phase 7 offline evaluation contracts.

Layered matcher (spec):
  1. file path must match exactly
  2. generated line within tolerance window
  3. category must match or map to an approved equivalent
  4. LLM judge evaluates semantic equivalence to the gold issue

Deterministic layers (1-3) decide candidacy; the judge only ever sees
structurally plausible pairs. Judge rationale is always persisted.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.agents.schemas import ReviewCategory, Severity

# was 3: reviewer anchors (often block-end) vs model anchors (often block-start)
LINE_TOLERANCE = 10

# Approved category equivalences (directed). Gold -> accepted predicted categories.
CATEGORY_EQUIVALENTS: dict[ReviewCategory, frozenset[ReviewCategory]] = {
  ReviewCategory.BUG_RISK: frozenset({ReviewCategory.BUG_RISK}),
  ReviewCategory.SECURITY: frozenset({ReviewCategory.SECURITY, ReviewCategory.BUG_RISK}),
  ReviewCategory.PERFORMANCE: frozenset({ReviewCategory.PERFORMANCE}),
  ReviewCategory.MAINTAINABILITY: frozenset({ReviewCategory.MAINTAINABILITY, ReviewCategory.STYLE}),
  ReviewCategory.STYLE: frozenset({ReviewCategory.STYLE, ReviewCategory.MAINTAINABILITY}),
  ReviewCategory.UI_REGRESSION: frozenset({ReviewCategory.UI_REGRESSION}),
}

# Severity agreement: exact match, or within one accepted level on this ladder.
SEVERITY_LADDER: list[Severity] = [
  Severity.LOW,
  Severity.MEDIUM,
  Severity.HIGH,
  Severity.CRITICAL,
]


class SystemName(StrEnum):
  BASELINE_A = "baseline_a"  # one-shot LLM, diff only
  BASELINE_B = "baseline_b"  # diff + repository RAG
  FINAL_AGENT = "final_agent"  # router + RAG + critic/retry + safe suppression
  FINAL_MULTIMODAL = "final_multimodal"  # final agent + screenshot analysis


class JudgeVerdict(StrEnum):
  EQUIVALENT = "equivalent"
  RELATED_BUT_DISTINCT = "related_but_distinct"
  DIFFERENT = "different"


class CandidatePair(BaseModel):
  """A generated comment that passed deterministic layers 1-3 for a gold issue."""

  gold_index: int
  generated_index: int
  line_delta: int


class JudgeRequest(BaseModel):
  example_id: str
  pairs: list[CandidatePair]


class JudgeDecision(BaseModel):
  gold_index: int
  generated_index: int
  verdict: JudgeVerdict
  rationale: str = Field(min_length=1)


class JudgeResponse(BaseModel):
  decisions: list[JudgeDecision]


class MatchRecord(BaseModel):
  """One judged pair, kept for the 20% human audit sample."""

  example_id: str
  gold_index: int
  generated_index: int | None  # None => gold issue unmatched (FN bookkeeping)
  verdict: JudgeVerdict | None  # None => no deterministic candidate existed
  matched: bool
  judge_rationale: str | None = None
  audited_by_human: bool = False
  human_agrees: bool | None = None


class CommentScore(BaseModel):
  example_id: str
  generated_index: int
  is_tp: bool
  is_grounded: bool  # claims supported by diff or retrieved context
  line_valid: bool  # points at a valid changed-line location
  # None when unmatched (no gold severity to compare)
  severity_agrees: bool | None = None


class ExampleMetrics(BaseModel):
  example_id: str
  system: SystemName
  attempt: int = 1  # generator/critic repair attempt that produced these comments
  tp: int = 0
  fp: int = 0
  fn: int = 0
  grounded_comments: int = 0
  line_valid_comments: int = 0
  severity_agreements: int = 0
  matched_with_severity: int = 0
  total_comments: int = 0
  expected_empty: bool = False
  predicted_empty: bool = False

  @property
  def precision(self) -> float | None:
    denom = self.tp + self.fp
    return self.tp / denom if denom else None

  @property
  def recall(self) -> float | None:
    denom = self.tp + self.fn
    return self.tp / denom if denom else None

  @property
  def f1(self) -> float | None:
    p, r = self.precision, self.recall
    if p is None or r is None or (p + r) == 0:
      return None
    return 2 * p * r / (p + r)


class AggregateMetrics(BaseModel):
  system: SystemName
  split: str
  examples: int
  tp: int = 0
  fp: int = 0
  fn: int = 0
  precision: float | None = None
  recall: float | None = None
  f1: float | None = None
  groundedness_rate: float | None = None
  line_validity_rate: float | None = None
  severity_agreement_rate: float | None = None
  negative_examples: int = 0
  correct_abstentions: int = 0
  no_comment_accuracy: float | None = None
  pass_at_1: float | None = None
  pass_at_2: float | None = None
  total_cost_usd: float = 0.0


class RoutingConfusion(BaseModel):
  """Confusion matrix for comment / no-comment routing."""

  true_comment: int = 0  # gold has issues, system commented
  false_comment: int = 0  # gold empty, system commented
  true_abstain: int = 0  # gold empty, system abstained
  false_abstain: int = 0  # gold has issues, system abstained
