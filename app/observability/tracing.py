"""Fail-open tracing primitives for review runs, evals, and loop events.

Every context manager yields a TraceHandle that stays safe to use when
Langfuse is off or down. Spans record wrapped exceptions as ERROR and
then re-raise them: tracing never swallows or causes review failures.

Targets the Langfuse Python SDK v4: propagate_attributes lives at module
level (not on the client), start_as_current_observation is the unified
observation API, and trace IDs come from create_trace_id.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.observability.attributes import attr_value
from app.observability.client import get_langfuse

try:
  # v4 module-level API. Guarded so this package imports without the SDK.
  from langfuse import propagate_attributes as _propagate_attributes
except Exception:
  _propagate_attributes = None

logger = structlog.get_logger(__name__)


@dataclass
class TraceHandle:
  """Handle that stays safe to use when tracing is off.

  observation is None in no-op mode, in which case update() does
  nothing. trace_id is set whenever a real trace was started.
  """

  observation: Any | None = None
  trace_id: str | None = None
  extra: dict[str, Any] = field(default_factory=dict)

  def update(self, **fields: Any) -> None:
    if self.observation is None:
      return
    try:
      self.observation.update(**{k: v for k, v in fields.items() if v is not None})
    except Exception as exc:
      logger.warning("langfuse_update_failed", error=str(exc)[:200])


async def _safe_aclose(stack: AsyncExitStack, name: str) -> None:
  try:
    await stack.aclose()
  except Exception as exc:
    logger.warning("langfuse_context_close_failed", trace=name, error=str(exc)[:200])


@asynccontextmanager
async def _observation(kind: str, name: str, **kwargs: Any) -> AsyncIterator[TraceHandle]:
  client = get_langfuse()
  if client is None:
    yield TraceHandle()
    return
  stack = AsyncExitStack()
  try:
    obs = stack.enter_context(
      client.start_as_current_observation(name=name, as_type=kind, **kwargs)
    )
  except Exception as exc:
    logger.warning("langfuse_observation_start_failed", observation=name, error=str(exc)[:200])
    await _safe_aclose(stack, name)
    yield TraceHandle()
    return
  handle = TraceHandle(observation=obs)
  try:
    yield handle
  except Exception as exc:
    handle.update(level="ERROR", status_message=str(exc)[:500])
    raise
  finally:
    await _safe_aclose(stack, name)


@asynccontextmanager
async def root_trace(
  name: str,
  *,
  trace_seed: str,
  input: Any = None,
  metadata: dict[str, Any] | None = None,
  session_id: str | None = None,
  version: str | None = None,
  tags: list[str] | None = None,
) -> AsyncIterator[TraceHandle]:
  """Generic root trace with a deterministic, seed-derived trace ID.

  Use this for eval runs and Phase 8 loop events. Review runs keep using
  review_run_trace, which delegates here. Namespace the seed per trace
  family (eval-run-<id>, promotion-<id>) so IDs from different tables
  can never collide.
  """
  client = get_langfuse()
  if client is None:
    yield TraceHandle()
    return

  stack = AsyncExitStack()
  try:
    trace_id = client.create_trace_id(seed=trace_seed)
    root = stack.enter_context(
      client.start_as_current_observation(
        name=name,
        as_type="span",
        input=input,
        metadata={k: v for k, v in (metadata or {}).items() if v is not None},
        trace_context={"trace_id": trace_id},
      )
    )
  except Exception as exc:
    logger.warning("langfuse_trace_start_failed", trace=name, error=str(exc)[:200])
    await _safe_aclose(stack, name)
    yield TraceHandle()
    return

  # Attribute propagation is best-effort on top of the root span: a
  # failure here must not lose the trace itself.
  attrs: dict[str, Any] = {}
  if session_id:
    attrs["session_id"] = attr_value(session_id)
  if version:
    attrs["version"] = attr_value(version)
  if tags:
    attrs["tags"] = [attr_value(tag) for tag in tags]
  if attrs and _propagate_attributes is not None:
    try:
      stack.enter_context(_propagate_attributes(**attrs))
    except Exception as exc:
      logger.warning("langfuse_attrs_failed", trace=name, error=str(exc)[:200])

  handle = TraceHandle(observation=root, trace_id=trace_id)
  try:
    yield handle
  except Exception as exc:
    handle.update(level="ERROR", status_message=str(exc)[:500])
    raise
  finally:
    await _safe_aclose(stack, name)


@asynccontextmanager
async def review_run_trace(
  *,
  review_run_id: Any,
  github_delivery_id: str | None = None,
  pr_metadata: dict[str, Any] | None = None,
  config_version: str | None = None,
  generator_prompt_version: str | None = None,
  critic_prompt_version: str | None = None,
) -> AsyncIterator[TraceHandle]:
  """One trace per review run, with a deterministic trace ID.

  The trace ID is seeded by the review-run ID, so the dashboard can
  deep-link to the Langfuse trace without storing anything new.
  """
  pr_metadata = pr_metadata or {}
  metadata = {
    "github_delivery_id": github_delivery_id,
    "review_run_id": str(review_run_id),
    "config_version": config_version,
    "generator_prompt_version": generator_prompt_version,
    "critic_prompt_version": critic_prompt_version,
    **pr_metadata,
  }
  repo = pr_metadata.get("repo")
  pr_number = pr_metadata.get("pr_number")
  session_id = None
  if repo is not None and pr_number is not None:
    session_id = f"{repo}#{pr_number}"
  tags = ["review_run"] + ([config_version] if config_version else [])
  async with root_trace(
    "review_run",
    trace_seed=str(review_run_id),
    input=pr_metadata or None,
    metadata=metadata,
    session_id=session_id,
    version=config_version,
    tags=tags,
  ) as handle:
    yield handle


def node_span(
  name: str,
  *,
  input: Any = None,
  metadata: dict[str, Any] | None = None,
) -> Any:
  """Span for deterministic work outside the LangGraph callback path.

  Inside the graph, node spans come from the LangChain CallbackHandler.
  Use this for eval harness stages, diagnosis, and promotion events.
  """
  return _observation("span", name, input=input, metadata=metadata or {})


def llm_generation(
  name: str,
  *,
  model: str,
  provider: str | None = None,
  input: Any = None,
  prompt_version: str | None = None,
  metadata: dict[str, Any] | None = None,
) -> Any:
  """Generation observation for one OpenRouter call.

  Attach results via handle.update(output=..., usage_details=...,
  cost_details=...). Retried calls produce one generation per attempt,
  failed attempts marked ERROR, which makes retry count visible.
  """
  meta = dict(metadata or {})
  if provider:
    meta["openrouter_provider"] = provider
  if prompt_version:
    meta["prompt_version"] = prompt_version
  return _observation("generation", name, model=model, input=input, metadata=meta)


def langgraph_handler() -> Any | None:
  """LangChain CallbackHandler for LangGraph invocation, or None.

  Pass via config={"callbacks": [handler]} at graph invocation. When a
  root trace is active, the graph trace nests under it.
  """
  client = get_langfuse()
  if client is None:
    return None
  try:
    from langfuse.langchain import CallbackHandler

    return CallbackHandler()
  except Exception as exc:
    logger.warning("langfuse_handler_failed", error=str(exc)[:200])
    return None


def score_trace(
  *,
  trace_id: str | None,
  name: str,
  value: float,
  comment: str | None = None,
  data_type: str = "NUMERIC",
) -> None:
  """Attach a numeric score to a trace (eval metrics). Fail-open."""
  if not trace_id:
    return
  client = get_langfuse()
  if client is None:
    return
  try:
    client.create_score(
      trace_id=trace_id,
      name=name,
      value=value,
      comment=comment,
      data_type=data_type,
    )
  except Exception as exc:
    logger.warning("langfuse_score_failed", score=name, error=str(exc)[:200])
