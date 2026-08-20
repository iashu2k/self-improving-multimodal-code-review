"""Trace attribute helpers with redaction.

Langfuse propagated attributes must be strings of at most 200 characters
with alphanumeric metadata keys, and PR payloads can contain private
text. Only a whitelisted, truncated subset of fields ever reaches a
trace.
"""

from __future__ import annotations

from typing import Any

MAX_ATTR_LEN = 200


def attr_value(value: Any, max_len: int = MAX_ATTR_LEN) -> str:
  """Coerce a value to a propagated-attribute-safe string."""
  return str(value)[:max_len]


def redacted_pr_metadata(
  *,
  repo: str,
  pr_number: int,
  head_sha: str,
  changed_files: int | None = None,
  additions: int | None = None,
  deletions: int | None = None,
) -> dict[str, Any]:
  """Whitelisted PR metadata for traces.

  PR titles, bodies, and author logins never leave the database through
  this path, and the head SHA is truncated to its short prefix.
  """
  return {
    "repo": repo,
    "pr_number": pr_number,
    "head_sha_prefix": head_sha[:8],
    "changed_files": changed_files,
    "additions": additions,
    "deletions": deletions,
  }
