"""Step 7: publisher payload construction (with Phase 2C markers) and
suppressor finalization, including retry-exhaustion cleanup."""

from types import SimpleNamespace

import pytest

from app.agents.graph import build_graph
from app.agents.qa_schemas import (
    CRITIC_REJECTED,
    RETRY_EXHAUSTED,
    QAResult,
    RiskLevel,
    RouteDecision,
    Verdict,
)
from tests.conftest import FakeStructuredClient
from tests.test_critic_qa import (
    make_fake_generate,
    make_result,
    make_verdict,
)
from tests.test_graph_skeleton import base_state
from tests.test_triage import CALC, DOCS


@pytest.fixture(autouse=True)
def passthrough_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.agents.validator.validate_review_comments",
        lambda *, result, files: SimpleNamespace(
            accepted_comments=result.comments, suppressed_comments=[]
        ),
    )


def route(**overrides) -> RouteDecision:
    return RouteDecision(risk_level=RiskLevel.MEDIUM, use_rag=False, **overrides)


async def test_publisher_builds_marked_payload(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.reviewer.generate_comments", make_fake_generate())
    client = FakeStructuredClient(
        {
            "route_decision": route(),
            "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
        }
    )
    final = await build_graph().ainvoke(base_state(run_id=42, changed_files=CALC, llm=client))

    assert final["should_publish"] is True
    assert final["abstain_reason"] is None

    comment = final["review_comments"][0]
    assert comment["path"] == "calc.py"
    assert comment["line"] == 2
    assert comment["side"] == "RIGHT"
    assert "review-forge" in comment["body"] and '"run_id":42' in comment["body"]
    assert "👍" in comment["body"]  # feedback prompt rendered

    assert "review-forge" in final["review_body"]
    assert "Posted 1 inline comment" in final["review_body"]


async def test_suppressor_honors_triage_abstain() -> None:
    final = await build_graph().ainvoke(base_state(changed_files=DOCS, llm=FakeStructuredClient()))
    assert final["should_publish"] is False
    assert final["abstain_reason"] == "docs_only"


async def test_suppressor_on_all_rejected(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.reviewer.generate_comments", make_fake_generate())
    client = FakeStructuredClient(
        {
            "route_decision": route(),
            "qa_result": QAResult(verdicts=[make_verdict(Verdict.REJECT)]),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    assert final["should_publish"] is False
    assert final["abstain_reason"] == "all_comments_suppressed"
    assert [s.reason for s in final["suppressed_comments"]] == [CRITIC_REJECTED]


async def test_suppressor_finalizes_retry_exhausted_candidates(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.reviewer.generate_comments", make_fake_generate())
    client = FakeStructuredClient(
        {
            "route_decision": route(),
            "qa_result": [
                QAResult(verdicts=[make_verdict(Verdict.REPAIR, instruction="fix it")])
                for _ in range(3)
            ],
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    assert final["should_publish"] is False
    assert final["retry_count"] == 2
    # the stranded, still-flagged candidate is suppressed — never publishable
    assert len(final["suppressed_comments"]) == 1
    assert final["suppressed_comments"][0].reason == RETRY_EXHAUSTED
    assert "fix it" in (final["suppressed_comments"][0].detail or "")


async def test_suppressor_uses_generator_abstain_reason(monkeypatch) -> None:
    from app.agents.schemas import ReviewResult

    async def abstaining_generate(
        *, files, pr_title, pr_body, client, model, contexts, feedback, review_focus
    ):
        return ReviewResult(
            summary="",
            comments=[],
            should_post_review=False,
            abstain_reason="nothing worth flagging",
        ), []

    monkeypatch.setattr("app.llm.reviewer.generate_comments", abstaining_generate)
    client = FakeStructuredClient({"route_decision": route()})
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    assert final["should_publish"] is False
    assert final["abstain_reason"] == "nothing worth flagging"


async def test_suppressed_audit_trail_accumulates_across_rounds(monkeypatch) -> None:
    """Deterministic + critic + exhaustion suppressions all land in one list."""
    from tests.test_critic_qa import make_candidate

    async def two_candidates(
        *, files, pr_title, pr_body, client, model, contexts, feedback, review_focus
    ):
        bad = make_candidate(title="no evidence")
        bad.evidence = []
        return make_result(), [make_candidate(), bad]

    monkeypatch.setattr("app.llm.reviewer.generate_comments", two_candidates)
    client = FakeStructuredClient(
        {
            "route_decision": route(),
            "qa_result": QAResult(verdicts=[make_verdict(Verdict.ACCEPT)]),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    reasons = [s.reason for s in final["suppressed_comments"]]
    assert reasons == ["qa_empty_evidence"]  # deterministic layer caught it
    assert final["should_publish"] is True  # the good one still posts
