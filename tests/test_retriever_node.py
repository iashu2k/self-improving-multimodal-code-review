from app.agents.graph import build_graph
from app.agents.qa_schemas import RiskLevel, RouteDecision
from tests.conftest import FakeStructuredClient
from tests.test_critic_qa import make_result
from tests.test_graph_skeleton import base_state
from tests.test_triage import CALC, DOCS


async def _noop_generate(
    *, files, pr_title, pr_body, client, model, contexts, feedback, review_focus
):
    """Retriever tests don't care about generation — keep the real
    reviewer (and its LLM call) out of the graph."""
    return make_result(), []


async def test_retriever_populates_context_and_caps(monkeypatch) -> None:
    from app.ingestion import retriever as retriever_module

    calls = []

    class FakeContext:
        file_path = "tests/test_calc.py"

    async def fake_retrieve(session, *, snapshot_id, query_text, llm, embedding_model):
        calls.append(query_text)
        return [FakeContext()] * 20  # more than the prompt cap

    monkeypatch.setattr(retriever_module, "hybrid_retrieve", fake_retrieve)
    monkeypatch.setattr(retriever_module, "MAX_CONTEXTS_FOR_PROMPT", 8)
    monkeypatch.setattr("app.llm.reviewer.generate_comments", _noop_generate)

    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(risk_level=RiskLevel.MEDIUM, use_rag=True),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    assert len(calls) == 1  # one query per changed file (under the cap)
    assert len(final["retrieved_context"]) == 8  # prompt cap enforced
    retrieve_event = next(e for e in final["events"] if e["node"] == "rag_retriever")
    assert retrieve_event["detail"]["context_count"] == 8


async def test_use_rag_false_bypasses_retriever(monkeypatch) -> None:
    monkeypatch.setattr("app.llm.reviewer.generate_comments", _noop_generate)

    client = FakeStructuredClient(
        {
            "route_decision": RouteDecision(risk_level=RiskLevel.LOW, use_rag=False),
        }
    )
    final = await build_graph().ainvoke(base_state(changed_files=CALC, llm=client))

    nodes = [e["node"] for e in final["events"]]
    assert "rag_retriever" not in nodes
    assert nodes[:2] == ["triage_router", "review_generator"]
    # no generation LLM call either — the stub replaced it
    assert all(c["schema_name"] == "route_decision" for c in client.calls)


async def test_docs_only_pr_goes_triage_straight_to_suppressor() -> None:
    final = await build_graph().ainvoke(base_state(changed_files=DOCS, llm=FakeStructuredClient()))
    nodes = [e["node"] for e in final["events"]]
    assert nodes == ["triage_router", "suppressor"]
    triage_event = final["events"][0]
    assert triage_event["detail"]["abstain"] is True
    assert triage_event["detail"]["reason"] == "docs_only"
