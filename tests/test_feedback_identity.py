import hashlib
import hmac

import pytest

from app.db.models.feedback import FeedbackActorType
from app.feedback.identity import classify_github_actor, hash_github_actor_login


def test_owner_is_maintainer() -> None:
  assert classify_github_actor("OWNER", site_admin=False) == FeedbackActorType.MAINTAINER


def test_member_is_maintainer() -> None:
  assert classify_github_actor("MEMBER", site_admin=False) == FeedbackActorType.MAINTAINER


def test_collaborator_is_maintainer() -> None:
  assert classify_github_actor("COLLABORATOR", site_admin=False) == FeedbackActorType.MAINTAINER


def test_contributor_is_developer() -> None:
  assert classify_github_actor("CONTRIBUTOR", site_admin=False) == FeedbackActorType.DEVELOPER


def test_unknown_association_defaults_to_developer() -> None:
  assert classify_github_actor("UNEXPECTED_VALUE", site_admin=False) == FeedbackActorType.DEVELOPER


def test_site_admin_is_admin() -> None:
  assert classify_github_actor(None, site_admin=True) == FeedbackActorType.ADMIN


def test_hash_is_stable_and_does_not_expose_raw_login() -> None:
  secret = "test-feedback-hmac-secret"
  expected = hmac.new(
    secret.encode(),
    b"github-actor:maintainer-user",
    hashlib.sha256,
  ).hexdigest()

  actual = hash_github_actor_login("Maintainer-User", secret=secret)

  assert actual == expected
  assert actual != "Maintainer-User"
  assert len(actual) == 64


def test_hash_normalizes_whitespace_and_case() -> None:
  secret = "test-feedback-hmac-secret"

  assert hash_github_actor_login(
    " maintainer-user ",
    secret=secret,
  ) == hash_github_actor_login(
    "MAINTAINER-USER",
    secret=secret,
  )


def test_hash_rejects_blank_login() -> None:
  with pytest.raises(ValueError, match="actor login must not be blank"):
    hash_github_actor_login("   ", secret="test-feedback-hmac-secret")


def test_hash_rejects_blank_secret() -> None:
  with pytest.raises(ValueError, match="feedback hash secret must not be blank"):
    hash_github_actor_login("maintainer-user", secret="   ")
