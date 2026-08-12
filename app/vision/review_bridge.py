from __future__ import annotations

from app.agents.schemas import ReviewCategory, ReviewComment, Severity
from app.vision.analyzer import GroundedObservation


def build_visual_review_comments(
  grounded: list[GroundedObservation],
) -> list[ReviewComment]:
  """
  Turn grounded visual observations into ReviewComment objects that can
  flow through the existing Phase 4 review graph / publisher.

  For now, use a simple mapping:
  - layout_overflow -> HIGH · UI_REGRESSION
  - others -> MEDIUM · UI_REGRESSION
  """
  comments: list[ReviewComment] = []

  for go in grounded:
    obs = go.observation

    severity = Severity.HIGH if obs.type == "layout_overflow" else Severity.MEDIUM

    category = ReviewCategory.UI_REGRESSION

    title = f"Visual regression: {obs.type.value.replace('_', ' ')}"
    body = (
      f"{obs.description}\n\n"
      f"Visual evidence: {obs.visual_evidence}\n"
      f"Grounded to {go.file_path} lines {sorted(go.line_numbers)[:5]}"
    )

    # Anchor to the first line for now; GitHub requires a single line.
    line = sorted(go.line_numbers)[0]

    comments.append(
      ReviewComment(
        file_path=go.file_path,
        line=line,
        side="RIGHT",
        title=title,
        body=body,
        severity=severity,
        category=category,
        evidence=[f"{go.file_path}:{ln}" for ln in sorted(go.line_numbers)[:5]],
        suggested_fix=None,
        confidence=0.8,
      )
    )

  return comments
