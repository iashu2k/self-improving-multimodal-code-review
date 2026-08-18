import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_feedback_hash_secret_defaults_to_application_secret() -> None:
  settings = Settings(
    _env_file=None,
    secret_key="a" * 32,
  )

  assert settings.feedback_actor_hash_secret == "a" * 32


def test_explicit_feedback_hash_secret_is_preferred() -> None:
  settings = Settings(
    _env_file=None,
    secret_key="a" * 32,
    feedback_hash_secret="b" * 32,
  )

  assert settings.feedback_actor_hash_secret == "b" * 32


def test_production_requires_dedicated_feedback_hash_secret() -> None:
  with pytest.raises(ValidationError, match="FEEDBACK_HASH_SECRET"):
    Settings(
      _env_file=None,
      app_env="production",
      secret_key="a" * 32,
    )


def test_production_accepts_dedicated_feedback_hash_secret() -> None:
  settings = Settings(
    _env_file=None,
    app_env="production",
    secret_key="a" * 32,
    feedback_hash_secret="b" * 32,
  )

  assert settings.feedback_actor_hash_secret == "b" * 32
