"""Fail-open contract tests for app.observability.

No Langfuse server or network access is required: the client is stubbed
and the v4 module-level propagate_attributes is replaced with a no-op,
so every path must degrade to a no-op that cannot raise.
"""

from __future__ import annotations

from typing import Any

import pytest

import app.observability.tracing as tracing
from app.observability import client as client_module
from app.observability.client import flush, get_langfuse, is_enabled
from app.observability.tracing import (
  langgraph_handler,
  llm_generation,
  node_span,
  review_run_trace,
  root_trace,
  score_trace,
)


class FakeObservation:
  def __init__(self) -> None:
    self.updates: list[dict[str, Any]] = []
    self.fail_on_update = False

  def update(self, **fields: Any) -> None:
    if self.fail_on_update:
      raise RuntimeError("ingest down")
    self.updates.append(fields)

  def __enter__(self) -> FakeObservation:
    return self

  def __exit__(self, *args: Any) -> bool:
    return False


class FakeClient:
  def __init__(self, **kwargs: Any) -> None:
    self.kwargs = kwargs
    self.observations: list[FakeObservation] = []
    self.scores: list[dict[str, Any]] = []
    self.flushed = False

  def create_trace_id(self, seed: str | None = None) -> str:
    return "a" * 32

  def start_as_current_observation(self, **kwargs: Any) -> FakeObservation:
    obs = FakeObservation()
    self.observations.append(obs)
    return obs

  def create_score(self, **kwargs: Any) -> None:
    self.scores.append(kwargs)

  def flush(self) -> None:
    self.flushed = True


class EnabledSettings:
  langfuse_enabled = True
  langfuse_public_key = "pk-test"
  langfuse_secret_key = "sk-test"
  langfuse_host = "http://localhost:3000"


class DisabledSettings:
  langfuse_enabled = False
  langfuse_public_key = None
  langfuse_secret_key = None
  langfuse_host = "http://localhost:3000"


@pytest.fixture(autouse=True)
def _reset_client() -> Any:
  client_module.reset_for_tests()
  yield
  client_module.reset_for_tests()


@pytest.fixture(autouse=True)
def _stub_propagate_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
  """Keep the real v4 propagate_attributes out of tests."""
  monkeypatch.setattr(tracing, "_propagate_attributes", lambda **kwargs: FakeObservation())


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
  client = FakeClient()
  monkeypatch.setattr(tracing, "get_langfuse", lambda: client)
  return client


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(client_module, "get_settings", lambda: DisabledSettings())
  assert not is_enabled()
  assert get_langfuse() is None


def test_sdk_missing_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(client_module, "get_settings", lambda: EnabledSettings())
  monkeypatch.setattr(client_module, "_load_sdk", lambda: None)
  assert get_langfuse() is None


def test_init_failure_is_cached_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(client_module, "get_settings", lambda: EnabledSettings())

  class BoomSDK:
    class Langfuse:
      def __init__(self, **kwargs: Any) -> None:
        raise ConnectionError("langfuse down")

  monkeypatch.setattr(client_module, "_load_sdk", lambda: BoomSDK)
  assert get_langfuse() is None
  assert get_langfuse() is None  # cached failure, no per-call retry storm


@pytest.mark.asyncio
async def test_review_run_trace_disabled_is_noop(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  monkeypatch.setattr(tracing, "get_langfuse", lambda: None)
  ran = False
  async with review_run_trace(review_run_id="run-1") as handle:
    ran = True
    assert handle.trace_id is None
    handle.update(output={"ignored": True})
  assert ran


@pytest.mark.asyncio
async def test_review_run_trace_happy_path(fake_client: FakeClient) -> None:
  async with review_run_trace(
    review_run_id="run-1",
    github_delivery_id="delivery-1",
    pr_metadata={"repo": "o/r", "pr_number": 5},
    config_version="v1.1",
  ) as handle:
    assert handle.trace_id == "a" * 32
    handle.update(output={"status": "completed"})
  root = fake_client.observations[0]
  assert any(u.get("output") == {"status": "completed"} for u in root.updates)


@pytest.mark.asyncio
async def test_body_exception_reraises_and_marks_error(fake_client: FakeClient) -> None:
  with pytest.raises(ValueError, match="boom"):
    async with review_run_trace(review_run_id="run-1"):
      raise ValueError("boom")
  root = fake_client.observations[0]
  assert any(u.get("level") == "ERROR" for u in root.updates)


@pytest.mark.asyncio
async def test_root_trace_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(tracing, "get_langfuse", lambda: None)
  async with root_trace("eval_run", trace_seed="eval-run-1") as handle:
    assert handle.trace_id is None
    handle.update(output={"ignored": True})


@pytest.mark.asyncio
async def test_root_trace_happy_path(fake_client: FakeClient) -> None:
  async with root_trace(
    "eval_run",
    trace_seed="eval-run-7",
    metadata={"eval_run_id": 7},
    version="v9",
    tags=["eval", "validation"],
  ) as handle:
    assert handle.trace_id == "a" * 32
    handle.update(output={"precision": 0.5})
  root = fake_client.observations[0]
  assert any(u.get("output") == {"precision": 0.5} for u in root.updates)


@pytest.mark.asyncio
async def test_root_trace_survives_attribute_failure(
  fake_client: FakeClient,
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  def boom(**kwargs: Any) -> Any:
    raise RuntimeError("attrs down")

  monkeypatch.setattr(tracing, "_propagate_attributes", boom)
  async with root_trace("eval_run", trace_seed="eval-run-8", session_id="o/r#5") as handle:
    assert handle.trace_id == "a" * 32
    handle.update(output={"ok": True})
  root = fake_client.observations[0]
  assert any(u.get("output") == {"ok": True} for u in root.updates)


@pytest.mark.asyncio
async def test_node_span_fail_open_on_start_error(
  monkeypatch: pytest.MonkeyPatch,
) -> None:
  class BrokenClient(FakeClient):
    def start_as_current_observation(self, **kwargs: Any) -> FakeObservation:
      raise ConnectionError("down")

  monkeypatch.setattr(tracing, "get_langfuse", lambda: BrokenClient())
  async with node_span("triage_router") as span:
    span.update(output={})  # no-op, must not raise


@pytest.mark.asyncio
async def test_generation_update_failure_swallowed(fake_client: FakeClient) -> None:
  async with llm_generation("review_generator", model="qwen/qwen3-coder-next") as gen:
    fake_client.observations[0].fail_on_update = True
    gen.update(output={}, usage_details={"total": 10})  # must not raise


def test_score_trace_requires_trace_id(fake_client: FakeClient) -> None:
  score_trace(trace_id=None, name="precision", value=0.5)
  assert fake_client.scores == []


def test_score_trace_records(fake_client: FakeClient) -> None:
  score_trace(trace_id="a" * 32, name="precision", value=0.5, comment="validation r1")
  assert fake_client.scores[0]["name"] == "precision"


def test_flush_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
  class FlushBoom(FakeClient):
    def flush(self) -> None:
      raise ConnectionError("down")

  monkeypatch.setattr(client_module, "get_langfuse", lambda: FlushBoom())
  flush()


def test_langgraph_handler_none_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(tracing, "get_langfuse", lambda: None)
  assert langgraph_handler() is None
