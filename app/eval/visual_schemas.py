"""Phase 6B: visual ground-truth extension for golden examples.

Visual cases are GoldenExamples whose defect is only detectable from a
rendered screenshot. Adds observation-level ground truth WITHOUT editing
golden_schemas.py — VisualGoldenExample subclasses GoldenExample.
Observation types/severities reused from app.vision.schemas (don't redefine).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.eval.golden_schemas import GoldenExample
from app.vision.schemas import ObservationType, SeverityHint


class ExpectedObservation(BaseModel):
  """Semantic expectation for one VisualObservation.

  Phase 7 matching: the analyzer's observation must share `type`, name
  `element`, and its visual_evidence must cite every token in
  evidence_must_mention (case-insensitive). Not a literal string match.
  """

  type: ObservationType
  severity_hint: SeverityHint
  element: str
  edge: str | None = Field(default=None, description="left|right|top|bottom if directional")
  evidence_must_mention: list[str] = Field(default_factory=list)


class VisualGroundTruth(BaseModel):
  baseline_shot: str = Field(description="path relative to data/golden/")
  pr_shot: str = Field(description="path relative to data/golden/")
  viewport: str = Field(description="primary annotated viewport: mobile | desktop")
  expected_observations: list[ExpectedObservation] = Field(default_factory=list)
  expected_empty: bool
  ground_truth_source_line: str = Field(description="path:line of the defect-causing change")

  @model_validator(mode="after")
  def _empty_consistent(self) -> VisualGroundTruth:
    if self.expected_empty and self.expected_observations:
      raise ValueError("expected_empty=True with non-empty expected_observations")
    if not self.expected_empty and not self.expected_observations:
      raise ValueError("expected_empty=False requires >= 1 expected observation")
    return self


class VisualGoldenExample(GoldenExample):
  """GoldenExample with screenshot-level ground truth attached."""

  visual: VisualGroundTruth
