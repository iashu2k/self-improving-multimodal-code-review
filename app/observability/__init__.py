"""Observability package: Langfuse tracing, fail-open by design.

Engineering decision 29: observability is never on the fail-closed path
of a review run. Every public helper here degrades to a no-op when
Langfuse is disabled, uninstalled, misconfigured, or unreachable.
"""

from app.observability.attributes import attr_value, redacted_pr_metadata
from app.observability.client import flush, get_langfuse, is_enabled
from app.observability.tracing import (
  langgraph_handler,
  llm_generation,
  node_span,
  review_run_trace,
  root_trace,
  score_trace,
)

__all__ = [
  "attr_value",
  "flush",
  "get_langfuse",
  "is_enabled",
  "langgraph_handler",
  "llm_generation",
  "node_span",
  "redacted_pr_metadata",
  "review_run_trace",
  "root_trace",
  "score_trace",
]
