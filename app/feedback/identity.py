import hashlib
import hmac

from app.db.models.feedback import FeedbackActorType

MAINTAINER_ASSOCIATIONS = {
  "OWNER",
  "MEMBER",
  "COLLABORATOR",
}


def classify_github_actor(
  author_association: str | None,
  *,
  site_admin: bool,
) -> FeedbackActorType:
  if site_admin:
    return FeedbackActorType.ADMIN

  if author_association in MAINTAINER_ASSOCIATIONS:
    return FeedbackActorType.MAINTAINER

  return FeedbackActorType.DEVELOPER


def hash_github_actor_login(
  actor_login: str,
  *,
  secret: str,
) -> str:
  normalized_login = actor_login.strip().lower()
  normalized_secret = secret.strip()

  if not normalized_login:
    raise ValueError("actor login must not be blank")
  if not normalized_secret:
    raise ValueError("feedback hash secret must not be blank")

  return hmac.new(
    normalized_secret.encode(),
    f"github-actor:{normalized_login}".encode(),
    hashlib.sha256,
  ).hexdigest()
