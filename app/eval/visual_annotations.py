"""Phase 6B: annotation schema for the golden visual examples.

Spec: handover section 5.2. One JSON per example at
data/golden/visual/annotations/<case_id>.json.

Matching philosophy: expected_observations are SEMANTIC expectations, not
literal strings. The Phase 7 eval harness checks that the analyzer's
VisionObservation has the same `type`, names the same element, and that its
visual_evidence cites every token in `evidence_must_mention`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class ExpectedObservation(BaseModel):
  type: str = Field(description="VisionObservation.type, e.g. layout_overflow")
  severity: str = Field(description="expected severity_hint: low | medium | high")
  element: str = Field(description="UI element the observation must name")
  edge: str | None = Field(default=None, description="left | right | top | bottom, if directional")
  evidence_must_mention: list[str] = Field(
    default_factory=list,
    description="substrings the model's visual_evidence must cite (case-insensitive)",
  )


class VisualCaseAnnotation(BaseModel):
  id: str
  pr_title: str
  changed_files: list[str]
  diff_ref: str = Field(description="unified diff path, relative to data/golden/visual/")
  baseline_shot: str = Field(description="relative to data/golden/visual/")
  pr_shot: str = Field(description="relative to data/golden/visual/")
  viewport: str = Field(description="primary annotated viewport: mobile | desktop")
  expected_observations: list[ExpectedObservation] = Field(default_factory=list)
  expected_empty: bool
  ground_truth_source_line: str = Field(description="path:line of the defect-causing change")
  notes: str = ""

  @model_validator(mode="after")
  def _empty_consistent(self) -> VisualCaseAnnotation:
    if self.expected_empty and self.expected_observations:
      raise ValueError(f"{self.id}: expected_empty=True with non-empty expected_observations")
    if not self.expected_empty and not self.expected_observations:
      raise ValueError(f"{self.id}: expected_empty=False requires >= 1 expected observation")
    return self
