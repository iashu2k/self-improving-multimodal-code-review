# app/vision/schemas.py
"""Vision model contracts.

The vision model produces STRUCTURED VISUAL OBSERVATIONS — it never reviews
code. Grounding an observation back to a changed CSS/TSX line is the
generator's job (policy rule 11), and the same validator that gates every
other comment enforces the anchor. One schema, two consumers: this drives
both the OpenRouter JSON Schema request and response validation.
"""

from enum import StrEnum

from pydantic import BaseModel, Field


class ObservationType(StrEnum):
  LAYOUT_OVERFLOW = "layout_overflow"
  CONTRAST = "wrong_color_contrast"
  HIDDEN_ELEMENT = "hidden_element"
  BROKEN_ALIGNMENT = "broken_alignment"
  OTHER = "other"


class SeverityHint(StrEnum):
  LOW = "low"
  MEDIUM = "medium"
  HIGH = "high"


class VisualObservation(BaseModel):
  """One visually verified problem.

  visual_evidence must describe something concrete in the rendered page
  (clipped edges, pixel positions, invisible text) — never a code cause.
  """

  type: ObservationType
  severity_hint: SeverityHint
  description: str
  visual_evidence: str


class VisionResult(BaseModel):
  """Structured output of the vision analyzer.

  Uncertainties are first-class: anything the model is unsure about goes
  here instead of becoming an observation (abstention is a feature).
  """

  page_loaded: bool
  observations: list[VisualObservation] = Field(default_factory=list, max_length=5)
  uncertainties: list[str] = Field(default_factory=list)
