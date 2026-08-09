from app.agents.graph import MAX_RETRY_COUNT, build_graph, route_after_qa
from app.agents.qa_schemas import RouteDecision


def base_state(**overrides):
    state = {
        "run_id": 1,
        "pr_number": 1,
        "commit_sha": "abc123",
        "pr_title": "t",
        "pr_body": "b",
        "config_version": "v1",
        "diff": "",
        "changed_files": [],
        "snapshot_id": None,
        "ui_screenshot_url": None,
        "retry_count": 0,
        "accepted_comments": [],
        "suppressed_comments": [],
        "errors": [],
        "events": [],
        "session": None,
        "llm": None,
        "router_model": "m",
        "review_model": "m",
        "critic_model": "m",
        "embedding_model": "m",
    }
    return state | overrides


async def test_abstain_route_goes_straight_to_suppressor() -> None:
    final = await build_graph().ainvoke(
        base_state(
            route=RouteDecision(risk_level="low", abstain=True, reason="docs only"),
        )
    )
    nodes = [e["node"] for e in final["events"]]
    assert nodes == ["triage_router", "suppressor"]


# --- route_after_qa: the exact bounded-retry policy, as a pure function ---


def _qa_state(*, accepted: int, needs_repair: bool, retry_count: int):
    return {
        "accepted_comments": [object()] * accepted,
        "needs_repair": needs_repair,
        "retry_count": retry_count,
    }


def test_qa_routes_accepted_to_publisher() -> None:
    assert route_after_qa(_qa_state(accepted=1, needs_repair=False, retry_count=0)) == "publisher"


def test_qa_routes_repair_under_cap_to_repair_generator() -> None:
    state = _qa_state(accepted=0, needs_repair=True, retry_count=MAX_RETRY_COUNT - 1)
    assert route_after_qa(state) == "repair_generator"


def test_qa_routes_repair_at_cap_to_suppressor() -> None:
    state = _qa_state(accepted=0, needs_repair=True, retry_count=MAX_RETRY_COUNT)
    assert route_after_qa(state) == "suppressor"


def test_qa_routes_no_accepted_comments_to_suppressor() -> None:
    assert route_after_qa(_qa_state(accepted=0, needs_repair=False, retry_count=0)) == "suppressor"


def test_mermaid_diagram_for_readme() -> None:
    mermaid = build_graph().get_graph().draw_mermaid()
    assert "critic_qa" in mermaid and "repair_generator" in mermaid
    print(mermaid)  # paste into README in Step 9
