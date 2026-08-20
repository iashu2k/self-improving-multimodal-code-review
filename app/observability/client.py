"""Langfuse client lifecycle.

Fail-open contract: any failure in here returns None or no-ops and logs
a warning. A Langfuse outage must never break a review run.
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import structlog

from app.core.config import get_settings

logger = structlog.get_logger(__name__)

_client: Any | None = None
_init_attempted = False


def _load_sdk() -> ModuleType | None:
  """Import langfuse lazily so the package works without the dependency."""
  try:
    import langfuse
  except ImportError:
    logger.warning("langfuse_sdk_missing", hint="uv add 'langfuse>=3,<4'")
    return None
  return langfuse


def is_enabled() -> bool:
  settings = get_settings()
  return bool(
    settings.langfuse_enabled and settings.langfuse_public_key and settings.langfuse_secret_key
  )


def get_langfuse() -> Any | None:
  """Return the singleton Langfuse client, or None when unavailable.

  Initialization is attempted at most once per process; a failed attempt
  is cached so a down server cannot cause a retry storm per LLM call.
  """
  global _client, _init_attempted
  if _client is not None:
    return _client
  if _init_attempted:
    return None
  _init_attempted = True
  if not is_enabled():
    return None
  sdk = _load_sdk()
  if sdk is None:
    return None
  try:
    settings = get_settings()
    _client = sdk.Langfuse(
      public_key=settings.langfuse_public_key,
      secret_key=settings.langfuse_secret_key,
      host=settings.langfuse_host,
    )
  except Exception as exc:
    logger.warning("langfuse_init_failed", error=str(exc)[:200])
    return None
  return _client


def flush() -> None:
  """Flush buffered spans. Safe to call when disabled or down."""
  client = get_langfuse()
  if client is None:
    return
  try:
    client.flush()
  except Exception as exc:
    logger.warning("langfuse_flush_failed", error=str(exc)[:200])


def reset_for_tests() -> None:
  """Reset the singleton so tests can vary settings and SDK state."""
  global _client, _init_attempted
  _client = None
  _init_attempted = False
