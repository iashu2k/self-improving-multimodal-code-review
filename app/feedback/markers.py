import json
import re
from dataclasses import dataclass

from app.db.models.feedback import FeedbackLabel, FeedbackTargetType

MARKER_PATTERN = re.compile(
  r"<!--\s*review-forge\s+(\{.*?\})\s*-->",
  re.DOTALL,
)

COMMAND_PATTERN = re.compile(
  r"^\s*/review-ai\s+([a-z-]+)(?:\s+(.*?))?\s*$",
  re.IGNORECASE,
)

COMMAND_LABELS = {
  "helpful": FeedbackLabel.HELPFUL,
  "not-helpful": FeedbackLabel.NOT_ACTIONABLE,
  "false-positive": FeedbackLabel.FALSE_POSITIVE,
  "wrong-severity": FeedbackLabel.WRONG_SEVERITY,
  "not-actionable": FeedbackLabel.NOT_ACTIONABLE,
  "missing-context": FeedbackLabel.MISSING_CONTEXT,
  "duplicate": FeedbackLabel.DUPLICATE,
}


@dataclass(frozen=True)
class ReviewForgeMarker:
  run_id: int
  target_type: FeedbackTargetType
  file_path: str | None = None
  line: int | None = None


@dataclass(frozen=True)
class ParsedFeedbackCommand:
  label: FeedbackLabel
  free_text: str | None = None


def _is_positive_int(value: object) -> bool:
  return isinstance(value, int) and not isinstance(value, bool) and value > 0


def parse_review_forge_marker(body: str) -> ReviewForgeMarker | None:
  matches = MARKER_PATTERN.findall(body)
  if len(matches) != 1:
    return None

  try:
    payload = json.loads(matches[0])
  except json.JSONDecodeError:
    return None

  if not isinstance(payload, dict):
    return None

  run_id = payload.get("run_id")
  if not _is_positive_int(run_id):
    return None

  kind = payload.get("kind")
  file_path = payload.get("file")
  line = payload.get("line")

  if kind == "summary":
    if file_path is not None or line is not None:
      return None
    return ReviewForgeMarker(
      run_id=run_id,
      target_type=FeedbackTargetType.REVIEW_SUMMARY,
    )

  if kind is not None:
    return None

  if not isinstance(file_path, str) or not file_path.strip():
    return None
  if not _is_positive_int(line):
    return None

  return ReviewForgeMarker(
    run_id=run_id,
    target_type=FeedbackTargetType.COMMENT,
    file_path=file_path,
    line=line,
  )


def parse_feedback_command(body: str) -> ParsedFeedbackCommand | None:
  match = COMMAND_PATTERN.match(body)
  if match is None:
    return None

  command_label = match.group(1).lower()
  label = COMMAND_LABELS.get(command_label)
  if label is None:
    return None

  free_text = match.group(2)
  if free_text is not None:
    free_text = free_text.strip() or None

  return ParsedFeedbackCommand(label=label, free_text=free_text)
