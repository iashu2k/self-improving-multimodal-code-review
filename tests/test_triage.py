import pytest

from app.agents.qa_schemas import RiskLevel, RouteDecision
from app.agents.triage import decide_route
from app.core.config import settings
from app.github.diff_parser import parse_unified_diff
from app.llm.router import ROUTER_SYSTEM_PROMPT, route_pr
from tests.conftest import FakeStructuredClient


def files_for(path: str):
    diff = (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1,1 +1,2 @@\n"
        " existing\n"
        "+added line\n"
    )
    return parse_unified_diff(diff)


CALC = files_for("calc.py")
DOCS = files_for("README.md")
AUTH = files_for("app/auth/login.py")


async def test_no_files_abstains_without_llm() -> None:
    client = FakeStructuredClient()
    route = await decide_route(client=client, model="m", files=[], pr_title="t", pr_body="b")
    assert route.abstain and route.reason == "no_source_changes"
    assert client.calls == []  # zero LLM spend


async def test_docs_only_abstains_without_llm() -> None:
    client = FakeStructuredClient()
    route = await decide_route(client=client, model="m", files=DOCS, pr_title="t", pr_body="b")
    assert route.abstain and route.reason == "docs_only"
    assert client.calls == []


async def test_oversized_pr_abstains_manual_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_max_added_lines", 0)
    route = await decide_route(
        client=FakeStructuredClient(), model="m", files=CALC, pr_title="t", pr_body="b"
    )
    assert route.abstain and route.reason == "pr_too_large"


async def test_security_path_forces_security_focus() -> None:
    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(
                risk_level=RiskLevel.MEDIUM, review_focus=["correctness"]
            ),
        }
    )
    route = await decide_route(client=client, model="m", files=AUTH, pr_title="t", pr_body="b")
    assert "security" in route.review_focus  # policy override, model omitted it


async def test_use_vision_forced_off() -> None:
    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(risk_level=RiskLevel.LOW, use_vision=True),
        }
    )
    route = await decide_route(client=client, model="m", files=CALC, pr_title="t", pr_body="b")
    assert route.use_vision is False


async def test_router_model_abstention_is_honored() -> None:
    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(
                risk_level=RiskLevel.LOW, abstain=True, reason="typo fixes only"
            ),
        }
    )
    route = await decide_route(client=client, model="m", files=CALC, pr_title="t", pr_body="b")
    assert route.abstain and route.reason == "typo fixes only"


async def test_route_pr_prompt_carries_stats_and_metadata() -> None:
    decision = RouteDecision(risk_level=RiskLevel.MEDIUM, review_focus=["correctness"])
    client = FakeStructuredClient({"route_decision": decision})

    route = await route_pr(
        client=client, model="router-m", files=CALC, pr_title="Fix divide", pr_body="body text"
    )

    assert route == decision
    call = client.calls[0]
    assert call["model"] == "router-m"
    assert call["schema_name"] == "route_decision"
    assert call["messages"][0]["content"] == ROUTER_SYSTEM_PROMPT
    user = call["messages"][1]["content"]
    assert "calc.py" in user and "+1/-0" in user
    assert "Fix divide" in user
