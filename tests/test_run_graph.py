"""Step 8: run_review_graph wrapper — state plumbing and output mapping."""

from types import SimpleNamespace

import pytest

from app.agents.graph import run_review_graph
from app.agents.qa_schemas import QAResult, RiskLevel, RouteDecision, Verdict
from tests.conftest import FakeStructuredClient
from tests.test_critic_qa import make_fake_generate, make_verdict
from tests.test_triage import CALC, DOCS


@pytest.fixture(autouse=True)
def passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    "app.agents.validator.validate_review_comments",
    lambda *, result, files: SimpleNamespace(
      accepted_comments=result.comments, suppressed_comments=[]
    ),
  )


def make_call(files, llm):
  return run_review_graph(
    session=None,
    llm=llm,
    snapshot_id=None,
    run_id=7,
    pr_number=1,
    commit_sha="abc123",
    pr_title="t",
    pr_body="b",
    diff="",
    changed_files=files,
    config_version="v1",
    router_model="m",
    review_model="m",
    critic_model="m",
    embedding_model="m",
  )


async def test_publish_path_output_mapping(monkeypatch) -> None:
  monkeypatch.setattr("app.llm.reviewer.generate_comments", make_fake_generate())
  llm = FakeStructuredClient(
    {
      "route_decision": RouteDecision(risk_level=RiskLevel.MEDIUM, use_rag=False),
      "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
    }
  )
  out = await make_call(CALC, llm)

  assert out.should_publish is True
  assert len(out.accepted) == 1
  assert len(out.review_comments) == 1
  assert '"run_id":7' in out.review_comments[0]["body"]
  assert "review-forge" in out.review_body
  assert [e["node"] for e in out.events] == [
    "triage_router",
    "review_generator",
    "critic_qa",
    "publisher",
  ]


async def test_abstain_path_output_mapping() -> None:
  out = await make_call(DOCS, FakeStructuredClient())

  assert out.should_publish is False
  assert out.abstain_reason == "docs_only"
  assert out.accepted == [] and out.review_comments == []
  assert out.route.abstain is True


async def test_trace_id_and_retry_count_populated(monkeypatch) -> None:
  monkeypatch.setattr("app.llm.reviewer.generate_comments", make_fake_generate())
  llm = FakeStructuredClient(
    {
      "route_decision": RouteDecision(risk_level=RiskLevel.MEDIUM, use_rag=False),
      "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
    }
  )
  out = await make_call(CALC, llm)

  assert out.retry_count == 0
  assert out.route.risk_level == RiskLevel.MEDIUM
