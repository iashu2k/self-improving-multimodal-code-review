from dataclasses import dataclass
from datetime import UTC, datetime

from app.db.models.feedback import FeedbackLabel
from app.feedback.markers import parse_feedback_command


@dataclass(frozen=True)
class FeedbackCommandCandidate:
  installation_id: int
  repository_owner: str
  repository_name: str
  pr_number: int
  reply_comment_id: int
  parent_comment_id: int
  occurred_at: datetime
  actor_login: str
  actor_site_admin: bool
  actor_association: str | None
  label: FeedbackLabel
  free_text: str | None


def _is_positive_int(value: object) -> bool:
  return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _parse_utc_timestamp(value: object) -> datetime | None:
  if not isinstance(value, str):
    return None

  try:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
  except ValueError:
    return None

  if parsed.tzinfo is None:
    return None

  return parsed.astimezone(UTC)


def extract_feedback_command_candidate(
  github_event: str,
  payload: dict,
) -> FeedbackCommandCandidate | None:
  if github_event != "pull_request_review_comment":
    return None
  if payload.get("action") != "created":
    return None

  repository = payload.get("repository")
  installation = payload.get("installation")
  pull_request = payload.get("pull_request")
  comment = payload.get("comment")
  sender = payload.get("sender")

  if not all(
    isinstance(value, dict) for value in (repository, installation, pull_request, comment, sender)
  ):
    return None

  repository_name = repository.get("full_name")
  if not isinstance(repository_name, str) or repository_name.count("/") != 1:
    return None

  repository_owner, repository_repo = repository_name.split("/", 1)
  if not repository_owner or not repository_repo:
    return None

  installation_id = installation.get("id")
  pr_number = pull_request.get("number")
  reply_comment_id = comment.get("id")
  parent_comment_id = comment.get("in_reply_to_id")
  occurred_at = _parse_utc_timestamp(comment.get("created_at"))

  if not all(
    _is_positive_int(value)
    for value in (
      installation_id,
      pr_number,
      reply_comment_id,
      parent_comment_id,
    )
  ):
    return None

  if occurred_at is None:
    return None

  body = comment.get("body")
  if not isinstance(body, str):
    return None

  parsed_command = parse_feedback_command(body)
  if parsed_command is None:
    return None

  sender_type = sender.get("type")
  actor_login = sender.get("login")
  actor_site_admin = sender.get("site_admin", False)

  if sender_type == "Bot":
    return None
  if not isinstance(actor_login, str) or not actor_login.strip():
    return None
  if not isinstance(actor_site_admin, bool):
    return None

  actor_association = comment.get("author_association")
  if actor_association is not None and not isinstance(actor_association, str):
    return None

  return FeedbackCommandCandidate(
    installation_id=installation_id,
    repository_owner=repository_owner,
    repository_name=repository_repo,
    pr_number=pr_number,
    reply_comment_id=reply_comment_id,
    parent_comment_id=parent_comment_id,
    occurred_at=occurred_at,
    actor_login=actor_login,
    actor_site_admin=actor_site_admin,
    actor_association=actor_association,
    label=parsed_command.label,
    free_text=parsed_command.free_text,
  )
