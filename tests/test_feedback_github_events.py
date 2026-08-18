from datetime import UTC, datetime

from app.db.models.feedback import FeedbackLabel
from app.feedback.github_events import extract_feedback_command_candidate


def make_payload() -> dict:
  return {
    "action": "created",
    "repository": {"full_name": "owner/repo"},
    "installation": {"id": 42},
    "pull_request": {"number": 99},
    "comment": {
      "id": 700,
      "in_reply_to_id": 600,
      "body": "/review-ai false-positive Caller already validates this.",
      "created_at": "2026-08-15T19:41:00Z",
    },
    "sender": {
      "login": "maintainer-user",
      "type": "User",
      "site_admin": False,
    },
  }


def test_extracts_valid_reply_command_candidate() -> None:
  candidate = extract_feedback_command_candidate(
    "pull_request_review_comment",
    make_payload(),
  )

  assert candidate is not None
  assert candidate.installation_id == 42
  assert candidate.repository_owner == "owner"
  assert candidate.repository_name == "repo"
  assert candidate.pr_number == 99
  assert candidate.reply_comment_id == 700
  assert candidate.parent_comment_id == 600
  assert candidate.actor_login == "maintainer-user"
  assert candidate.actor_association is None
  assert candidate.label == FeedbackLabel.FALSE_POSITIVE
  assert candidate.free_text == "Caller already validates this."
  assert candidate.occurred_at == datetime(2026, 8, 15, 19, 41, tzinfo=UTC)
  assert candidate.actor_site_admin is False


def test_preserves_site_admin_status() -> None:
  payload = make_payload()
  payload["sender"]["site_admin"] = True

  candidate = extract_feedback_command_candidate(
    "pull_request_review_comment",
    payload,
  )

  assert candidate is not None
  assert candidate.actor_site_admin is True


def test_rejects_non_boolean_site_admin_status() -> None:
  payload = make_payload()
  payload["sender"]["site_admin"] = "false"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_preserves_author_association_when_present() -> None:
  payload = make_payload()
  payload["comment"]["author_association"] = "MEMBER"

  candidate = extract_feedback_command_candidate(
    "pull_request_review_comment",
    payload,
  )

  assert candidate is not None
  assert candidate.actor_association == "MEMBER"


def test_ignores_non_review_comment_webhook_event() -> None:
  assert extract_feedback_command_candidate("issue_comment", make_payload()) is None


def test_ignores_non_created_action() -> None:
  payload = make_payload()
  payload["action"] = "edited"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_ignores_non_command_comment_body() -> None:
  payload = make_payload()
  payload["comment"]["body"] = "This may be a false positive."

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_command_without_parent_comment_id() -> None:
  payload = make_payload()
  payload["comment"].pop("in_reply_to_id")

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_command_without_reply_comment_id() -> None:
  payload = make_payload()
  payload["comment"]["id"] = "700"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_bot_sender() -> None:
  payload = make_payload()
  payload["sender"]["type"] = "Bot"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_invalid_repository_name() -> None:
  payload = make_payload()
  payload["repository"]["full_name"] = "not-a-repository"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_missing_sender_login() -> None:
  payload = make_payload()
  payload["sender"]["login"] = ""

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_missing_comment_created_at() -> None:
  payload = make_payload()
  payload["comment"].pop("created_at")

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None


def test_rejects_invalid_comment_created_at() -> None:
  payload = make_payload()
  payload["comment"]["created_at"] = "not-a-timestamp"

  assert extract_feedback_command_candidate("pull_request_review_comment", payload) is None
