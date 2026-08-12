# tests/test_critic_qa.py
"""Critic & safety QA graph tests (Step 6): full-graph runs with a mocked
OpenRouter client. Includes the Phase 4 demo path: generator -> critic repair
-> repair generator -> critic accept -> publisher."""

from types import SimpleNamespace

import pytest

from app.agents.graph import build_graph
from app.agents.qa_schemas import (
  CRITIC_REJECTED,
  QA_EMPTY_EVIDENCE,
  QA_NO_VERDICT,
  QAResult,
  QAVerdict,
  RiskLevel,
  RouteDecision,
  Verdict,
)
from app.agents.schemas import ReviewCategory, ReviewComment, ReviewResult, Severity
from tests.conftest import FakeStructuredClient
from tests.test_graph_skeleton import base_state
from tests.test_triage import CALC

# --- shared factories (imported by test_generator_node.py and test_qa.py) ---


def make_candidate(
  title: str = "Silent float truncation",
  body: str | None = None,
  line: int = 2,
) -> ReviewComment:
  """Passes deterministic QA: >=8 words, a rationale marker, non-empty
  evidence, concise fix. Placement is monkeypatched out in these tests."""
  return ReviewComment(
    file_path="calc.py",
    line=line,
    side="RIGHT",
    severity=Severity.HIGH,
    category=ReviewCategory.BUG_RISK,
    title=title,
    body=body or "int() truncates the result because it floors toward zero, breaking callers.",
    evidence=["return int(a / b)"],
    suggested_fix="Return result unchanged or round().",
    confidence=0.9,
  )


def make_result() -> ReviewResult:
  return ReviewResult(
    summary="One issue found.",
    comments=[],
    should_post_review=True,
    abstain_reason=None,
  )


def make_verdict(
  action: Verdict,
  reason: str = "critic rationale",
  instruction: str | None = None,
  index: int = 0,
) -> QAVerdict:
  return QAVerdict(
    comment_index=index,
    verdict=action,
    grounded=action is not Verdict.REJECT,
    actionable=True,
    duplicate=False,
    policy_safe=True,
    reason=reason,
    repair_instruction=instruction,
  )


def make_fake_generate():
  """Returns 'original' candidate on first pass, 'repaired' when feedback
  is supplied. Records every call."""
  calls: list[dict] = []

  async def fake(*, files, pr_title, pr_body, client, model, contexts, feedback, review_focus):
    calls.append({"feedback": feedback, "focus": review_focus})
    title = "repaired" if feedback else "original"
    return make_result(), [make_candidate(title=title)]

  fake.calls = calls
  return fake


@pytest.fixture(autouse=True)
def passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(
    "app.agents.validator.validate_review_comments",
    lambda *, result, files: SimpleNamespace(
      accepted_comments=result.comments, suppressed_comments=[]
    ),
  )


def route() -> RouteDecision:
  # use_rag=False keeps the retriever (and its DB/embeddings) out of these tests
  return RouteDecision(risk_level=RiskLevel.MEDIUM, use_rag=False, review_focus=["correctness"])


async def run_graph(client: FakeStructuredClient, monkeypatch, generate=None):
  monkeypatch.setattr("app.llm.reviewer.generate_comments", generate or make_fake_generate())
  return await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))


# --- tests ---


async def test_accept_routes_to_publisher(monkeypatch) -> None:
  client = FakeStructuredClient(
    {
      "route_decision": route(),
      "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
    }
  )
  final = await run_graph(client, monkeypatch)

  assert len(final["accepted_comments"]) == 1
  assert final["accepted_comments"][0].title == "original"
  assert final["retry_count"] == 0
  assert final["suppressed_comments"] == []
  nodes = [e["node"] for e in final["events"]]
  assert nodes == ["triage_router", "review_generator", "critic_qa", "publisher"]


async def test_reject_suppresses_without_repair(monkeypatch) -> None:
  generate = make_fake_generate()
  client = FakeStructuredClient(
    {
      "route_decision": route(),
      "qa_result": QAResult(
        verdicts=[make_verdict(Verdict.REJECT, reason="claim not in diff or context")]
      ),
    }
  )
  final = await run_graph(client, monkeypatch, generate)

  assert final["accepted_comments"] == []
  assert len(final["suppressed_comments"]) == 1
  suppressed = final["suppressed_comments"][0]
  assert suppressed.reason == CRITIC_REJECTED
  assert "claim not in diff or context" in (suppressed.detail or "")
  nodes = [e["node"] for e in final["events"]]
  assert "repair_generator" not in nodes
  assert nodes[-1] == "suppressor"
  assert len(generate.calls) == 1  # no wasted regeneration


async def test_repair_then_accept_publishes_repaired_comment(monkeypatch) -> None:
  """The Phase 4 demo path: generator -> critic repair -> repair generator
  -> critic accept -> publisher."""
  generate = make_fake_generate()
  client = FakeStructuredClient(
    {
      "route_decision": route(),
      "qa_result": [
        QAResult(
          verdicts=[
            make_verdict(
              Verdict.REPAIR,
              reason="assumes a transaction the context doesn't establish",
              instruction="limit the claim to missing input validation on line 2",
            )
          ]
        ),
        QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
      ],
    }
  )
  final = await run_graph(client, monkeypatch, generate)

  assert len(final["accepted_comments"]) == 1
  assert final["accepted_comments"][0].title == "repaired"
  assert final["retry_count"] == 1
  # repair instructions actually reached the generator
  second_call_feedback = generate.calls[1]["feedback"]
  assert second_call_feedback is not None
  assert "limit the claim to missing input validation on line 2" in second_call_feedback
  nodes = [e["node"] for e in final["events"]]
  assert nodes == [
    "triage_router",
    "review_generator",
    "critic_qa",
    "repair_generator",
    "critic_qa",
    "publisher",
  ]


async def test_repair_exhaustion_stops_at_cap(monkeypatch) -> None:
  generate = make_fake_generate()
  client = FakeStructuredClient(
    {
      "route_decision": route(),
      "qa_result": [
        QAResult(verdicts=[make_verdict(Verdict.REPAIR, instruction="fix it")]) for _ in range(3)
      ],
    }
  )
  final = await run_graph(client, monkeypatch, generate)

  assert final["retry_count"] == 2
  assert len(generate.calls) == 3  # 1 initial + 2 repairs, then hard stop
  assert final["accepted_comments"] == []
  nodes = [e["node"] for e in final["events"]]
  assert nodes.count("repair_generator") == 2
  assert nodes[-1] == "suppressor"
  # the still-flagged comments are handed to the suppressor for
  # RETRY_EXHAUSTED finalization in Step 7
  assert len(final["candidate_comments"]) == 1


async def test_missing_verdict_fails_closed(monkeypatch) -> None:
  client = FakeStructuredClient(
    {
      "route_decision": route(),
      "qa_result": QAResult(verdicts=[]),  # critic judged nothing
    }
  )
  final = await run_graph(client, monkeypatch)

  assert final["accepted_comments"] == []
  assert len(final["suppressed_comments"]) == 1
  assert final["suppressed_comments"][0].reason == QA_NO_VERDICT


async def test_deterministic_short_circuit_skips_critic_llm_call(monkeypatch) -> None:
  async def generate_bad(
    *, files, pr_title, pr_body, client, model, contexts, feedback, review_focus
  ):
    comment = make_candidate()
    comment.evidence = []  # fails deterministic QA before the critic
    return make_result(), [comment]

  client = FakeStructuredClient({"route_decision": route()})
  final = await run_graph(client, monkeypatch, generate_bad)

  assert final["accepted_comments"] == []
  assert len(final["suppressed_comments"]) == 1
  assert final["suppressed_comments"][0].reason == QA_EMPTY_EVIDENCE
  # zero critic tokens spent on a deterministically-bad candidate
  assert all(c["schema_name"] == "route_decision" for c in client.calls)
