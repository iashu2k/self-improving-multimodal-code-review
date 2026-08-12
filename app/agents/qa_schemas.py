"""Phase 4 contracts: routing decisions, QA verdicts, suppression reasons.

Reason strings are a cross-layer contract (graph nodes, tests, job fixtures
hardcode them — gotcha #7). Grep before renaming.
"""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.agents.validator import SuppressedComment  # noqa: F401 — single shared type, re-exported

# Suppression reasons introduced by the QA loop. Validator reasons
# (e.g. "line_not_in_diff") stay in app/agents/validator.py.
CRITIC_REJECTED = "critic_rejected"
RETRY_EXHAUSTED = "retry_exhausted"
QA_NO_VERDICT = "qa_no_verdict"
QA_EMPTY_EVIDENCE = "qa_empty_evidence"
QA_NO_RATIONALE = "qa_no_rationale"
QA_FIX_TOO_LONG = "qa_fix_too_long"
QA_DUPLICATE = "qa_duplicate"


# --- Triage router output (LLM strategy pass; deterministic rules run first) ---


class RiskLevel(StrEnum):
  LOW = "low"
  MEDIUM = "medium"
  HIGH = "high"


class RouteDecision(BaseModel):
  risk_level: RiskLevel
  review_focus: list[str] = Field(default_factory=list)
  use_rag: bool = True
  use_vision: bool = False  # always False until Phase 5
  abstain: bool = False
  reason: str = ""


# --- Critic output: one verdict per candidate comment ---


class Verdict(StrEnum):
  ACCEPT = "accept"
  REPAIR = "repair"
  REJECT = "reject"


class QAVerdict(BaseModel):
  comment_index: int = Field(description="Index into the candidate comment list")
  verdict: Verdict
  grounded: bool = Field(description="Every claim supported by diff or context")
  actionable: bool = Field(description="Author knows what to change")
  duplicate: bool
  policy_safe: bool
  reason: str
  repair_instruction: str | None = Field(default=None, description="Required when verdict=repair")


class QAResult(BaseModel):
  verdicts: list[QAVerdict]


# --- Phase 5 placeholder, defined now so state doesn't migrate later ---


class VisionObservation(BaseModel):
  summary: str
  grounded_line: int | None = None
