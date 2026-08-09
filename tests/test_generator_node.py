"""Generator node tests (Step 5)."""

from types import SimpleNamespace

import pytest

from app.agents.graph import build_graph
from app.agents.qa_schemas import QAResult, RiskLevel, RouteDecision, Verdict
from tests.conftest import FakeStructuredClient
from tests.test_critic_qa import make_candidate, make_result, make_verdict
from tests.test_graph_skeleton import base_state
from tests.test_triage import CALC


@pytest.fixture(autouse=True)
def passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.validator.validate_review_comments",
        lambda *, result, files: SimpleNamespace(
            accepted_comments=result.comments, suppressed_comments=[]
        ),
    )


def make_generate(candidates_per_call):
    calls = []

    async def fake(*, files, pr_title, pr_body, client, model, contexts, feedback, review_focus):
        calls.append({"feedback": feedback, "focus": review_focus})
        return make_result(), candidates_per_call

    fake.calls = calls
    return fake


async def test_generator_node_populates_candidates_and_uses_route_focus(monkeypatch) -> None:
    fake = make_generate([make_candidate()])
    monkeypatch.setattr("app.llm.reviewer.generate_comments", fake)

    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(
                risk_level=RiskLevel.HIGH,
                use_rag=False,
                review_focus=["correctness", "security"],
            ),
            "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    # the critic accepted it, so it now lives in accepted_comments and the
    # active candidate set (repair queue) is drained
    assert len(final["accepted_comments"]) == 1
    assert final["accepted_comments"][0].title == "Silent float truncation"
    assert final["candidate_comments"] == []
    assert fake.calls[0] == {"feedback": None, "focus": ["correctness", "security"]}


async def test_generator_empty_candidates_skips_critic_llm_call(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.reviewer.generate_comments", make_generate([]))
    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(risk_level=RiskLevel.LOW, use_rag=False),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    nodes = [e["node"] for e in final["events"]]
    assert "critic_qa" in nodes and "suppressor" in nodes
    assert all(c["schema_name"] == "route_decision" for c in client.calls)


def test_generator_policy_is_in_system_prompt() -> None:
    from app.llm.reviewer import GENERATOR_POLICY

    for rule in (
        "ONLY the supplied changed lines",
        "silence is a success case",
        "Never invent runtime behavior",
        "No subjective style comments",
    ):
        assert rule in GENERATOR_POLICY
