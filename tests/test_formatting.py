import json
import re
from uuid import uuid4

from app.agents.schemas import ReviewCategory, ReviewComment, ReviewResult, Severity
from app.github.formatting import (
  format_comment_body,
  format_review_summary,
  marker_payload,
)

MARKER_RE = re.compile(r"<!-- review-forge (\{.*?\}) -->")


def make_comment() -> ReviewComment:
  return ReviewComment(
    file_path="calc.py",
    line=3,
    side="RIGHT",
    severity=Severity.HIGH,
    category=ReviewCategory.BUG_RISK,
    title="Silent float truncation",
    body="int() truncates the division result.",
    evidence=["return int(result)"],
    suggested_fix="Return result unchanged or use round().",
    confidence=0.9,
  )


def extract_marker(body: str) -> dict:
  match = MARKER_RE.search(body)
  assert match, f"no review-forge marker in body:\n{body}"
  return json.loads(match.group(1))


def test_format_comment_body_includes_severity_and_fix() -> None:
  body = format_comment_body(make_comment())

  assert "HIGH" in body
  assert "bug risk" in body
  assert "Silent float truncation" in body
  assert "Suggested fix" in body


def test_format_comment_body_includes_marker_with_run_id() -> None:
  body = format_comment_body(make_comment(), run_id=42)

  assert "👍" in body
  payload = extract_marker(body)
  assert payload == {"run_id": 42, "file": "calc.py", "line": 3}


def test_format_comment_body_omits_marker_without_run_id() -> None:
  body = format_comment_body(make_comment())

  assert "review-forge" not in body
  assert "👍" not in body


def test_format_review_summary_includes_marker_with_run_id() -> None:
  result = ReviewResult(
    summary="Found one real issue.",
    comments=[make_comment()],
    should_post_review=True,
    abstain_reason=None,
  )

  body = format_review_summary(result, run_id=7)

  assert "👍" in body
  payload = extract_marker(body)
  assert payload == {"run_id": 7, "kind": "summary"}


def test_format_review_summary_omits_marker_without_run_id() -> None:
  result = ReviewResult(
    summary="Found one real issue.",
    comments=[make_comment()],
    should_post_review=True,
    abstain_reason=None,
  )

  body = format_review_summary(result)

  assert "review-forge" not in body
  assert "👍" not in body


def test_marker_payload_is_html_comment() -> None:
  marker = marker_payload({"run_id": 1, "kind": "summary"})

  assert marker.startswith("<!-- review-forge ")
  assert marker.endswith("-->")
  assert json.loads(marker.removeprefix("<!-- review-forge ").removesuffix(" -->")) == {
    "run_id": 1,
    "kind": "summary",
  }


def test_marker_payload_serializes_uuid() -> None:
  run_id = uuid4()

  marker = marker_payload({"run_id": run_id, "file": "src/example.py", "line": 12})

  assert str(run_id) in marker
  assert '"file":"src/example.py"' in marker
  assert '"line":12' in marker
