from app.db.models.feedback import FeedbackLabel, FeedbackTargetType
from app.feedback.markers import parse_feedback_command, parse_review_forge_marker


def test_parses_inline_comment_marker() -> None:
  body = """
🟠 **[HIGH · bug risk]** Missing null guard

The client can receive a missing token.

<!-- review-forge {"file":"src/client.py","line":24,"run_id":17} -->
"""

  marker = parse_review_forge_marker(body)

  assert marker is not None
  assert marker.run_id == 17
  assert marker.target_type == FeedbackTargetType.COMMENT
  assert marker.file_path == "src/client.py"
  assert marker.line == 24


def test_parses_summary_marker() -> None:
  body = """
### 🤖 Self-Improving Multimodal Code Review

Posted 2 inline comments.

<!-- review-forge {"kind":"summary","run_id":17} -->
"""

  marker = parse_review_forge_marker(body)

  assert marker is not None
  assert marker.run_id == 17
  assert marker.target_type == FeedbackTargetType.REVIEW_SUMMARY
  assert marker.file_path is None
  assert marker.line is None


def test_rejects_marker_without_valid_positive_run_id() -> None:
  body = '<!-- review-forge {"file":"src/client.py","line":24,"run_id":"17"} -->'

  assert parse_review_forge_marker(body) is None


def test_rejects_marker_without_valid_positive_line() -> None:
  body = '<!-- review-forge {"file":"src/client.py","line":false,"run_id":17} -->'

  assert parse_review_forge_marker(body) is None


def test_rejects_summary_marker_with_inline_location() -> None:
  body = '<!-- review-forge {"kind":"summary","file":"src/client.py","line":24,"run_id":17} -->'

  assert parse_review_forge_marker(body) is None


def test_rejects_multiple_markers_as_ambiguous() -> None:
  body = """
<!-- review-forge {"kind":"summary","run_id":17} -->
<!-- review-forge {"kind":"summary","run_id":18} -->
"""

  assert parse_review_forge_marker(body) is None


def test_rejects_malformed_marker_json() -> None:
  body = "<!-- review-forge {not-json} -->"

  assert parse_review_forge_marker(body) is None


def test_returns_none_when_no_marker_exists() -> None:
  assert parse_review_forge_marker("Normal GitHub comment body.") is None


def test_parses_helpful_command() -> None:
  command = parse_feedback_command("/review-ai helpful")

  assert command is not None
  assert command.label == FeedbackLabel.HELPFUL
  assert command.free_text is None


def test_parses_not_helpful_command_with_explanation() -> None:
  command = parse_feedback_command("/review-ai not-helpful Caller already validates this state.")

  assert command is not None
  assert command.label == FeedbackLabel.NOT_ACTIONABLE
  assert command.free_text == "Caller already validates this state."


def test_parses_explicit_taxonomy_command() -> None:
  command = parse_feedback_command(
    "/review-ai wrong-severity This is low priority in this service."
  )

  assert command is not None
  assert command.label == FeedbackLabel.WRONG_SEVERITY
  assert command.free_text == "This is low priority in this service."


def test_command_is_case_insensitive() -> None:
  command = parse_feedback_command("/REVIEW-AI DUPLICATE Same finding as another comment.")

  assert command is not None
  assert command.label == FeedbackLabel.DUPLICATE
  assert command.free_text == "Same finding as another comment."


def test_rejects_command_embedded_in_prose() -> None:
  assert parse_feedback_command("I think /review-ai helpful is a useful command.") is None


def test_rejects_unknown_command_label() -> None:
  assert parse_feedback_command("/review-ai excellent") is None
