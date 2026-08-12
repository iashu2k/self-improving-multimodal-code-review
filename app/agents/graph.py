"""Phase 4 agent graph.

    triage_router ──abstain──> suppressor ──> END
          │proceed
          ▼
    rag_retriever (bypassed when route.use_rag is False)
          ▼
    review_generator ──> critic_qa ──┬── accept ─────────────> publisher ──> END
                                     ├── repair + retry<2 ──> repair_generator ──> critic_qa
                                     └── reject / retry==2 ──> suppressor ──> END

Bounded retry is structural: critic_qa is the only node that routes into
repair_generator, and route_after_qa refuses once retry_count hits the cap.

NOTE: generate_comments, the validator (via qa.py), hybrid_retrieve, and
decide_route are imported lazily inside nodes on purpose — tests monkeypatch
them at their home modules, which only works with call-time imports.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, StateGraph

from app.agents.qa_schemas import (
  CRITIC_REJECTED,
  QA_NO_VERDICT,
  RETRY_EXHAUSTED,
  SuppressedComment,
  Verdict,
)
from app.agents.schemas import ReviewComment
from app.agents.state import GraphEvent, ReviewGraphState

MAX_RETRY_COUNT = 2


async def triage_router_node(state: ReviewGraphState) -> dict:
  from app.agents.triage import decide_route

  route = await decide_route(
    client=state["llm"],
    model=state["router_model"],
    files=state["changed_files"],
    pr_title=state["pr_title"],
    pr_body=state["pr_body"],
  )
  return {
    "route": route,
    "events": [{"node": "triage_router", "detail": route.model_dump(mode="json")}],
  }


async def rag_retriever_node(state: ReviewGraphState) -> dict:
  from app.ingestion.retriever import (
    MAX_CONTEXT_QUERY_FILES,
    MAX_CONTEXTS_FOR_PROMPT,
    build_context_query,
    hybrid_retrieve,
  )

  contexts = []
  for changed_file in state["changed_files"][:MAX_CONTEXT_QUERY_FILES]:
    contexts.extend(
      await hybrid_retrieve(
        state["session"],
        snapshot_id=state["snapshot_id"],
        query_text=build_context_query(changed_file),
        llm=state["llm"],
        embedding_model=state["embedding_model"],
      )
    )
  contexts = contexts[:MAX_CONTEXTS_FOR_PROMPT]
  return {
    "retrieved_context": contexts,
    "events": [
      {
        "node": "rag_retriever",
        "detail": {
          "context_count": len(contexts),
          "sources": [getattr(c, "file_path", "?") for c in contexts],
        },
      }
    ],
  }


async def review_generator_node(state: ReviewGraphState) -> dict:
  from app.llm.reviewer import generate_comments

  result, candidates = await generate_comments(
    files=state["changed_files"],
    pr_title=state["pr_title"],
    pr_body=state["pr_body"],
    client=state["llm"],
    model=state["review_model"],
    contexts=state.get("retrieved_context", []),
    feedback=None,
    review_focus=state["route"].review_focus if state.get("route") else [],
  )
  return {
    "candidate_comments": candidates,
    "summary": result.summary,
    "abstain_reason": result.abstain_reason,
    "events": [
      {
        "node": "review_generator",
        "detail": {"candidate_count": len(candidates), "repaired": False},
      }
    ],
  }


async def critic_qa_node(state: ReviewGraphState) -> dict:
  candidates = state.get("candidate_comments", [])
  detail: dict = {"in": len(candidates)}
  if not candidates:
    return {
      "needs_repair": False,
      "events": [{"node": "critic_qa", "detail": detail}],
    }

  # --- deterministic checks first: placement (validator) + content QA ---
  from app.agents.qa import run_deterministic_qa

  survivors, det_suppressed = run_deterministic_qa(candidates, files=state["changed_files"])
  detail["deterministic"] = {
    "survivors": len(survivors),
    "suppressed": [s.reason for s in det_suppressed],
  }
  if not survivors:
    return {
      "candidate_comments": [],  # nothing survived QA — nothing to strand
      "suppressed_comments": det_suppressed,
      "needs_repair": False,
      "events": [{"node": "critic_qa", "detail": detail}],
    }

  # --- LLM critique on survivors only ---
  from app.llm.critic import critique_candidates

  qa = await critique_candidates(
    client=state["llm"],
    model=state["critic_model"],
    files=state["changed_files"],
    comments=survivors,
    contexts=state.get("retrieved_context", []),
  )

  # --- partition verdicts (fail closed: no verdict -> suppress) ---
  verdicts = {v.comment_index: v for v in qa.verdicts}
  accepted, qa_suppressed, repair_pairs = [], [], []
  for i, comment in enumerate(survivors):
    v = verdicts.get(i)
    if v is None:
      qa_suppressed.append(SuppressedComment(comment, QA_NO_VERDICT))
    elif v.verdict is Verdict.ACCEPT:
      accepted.append(comment)
    elif v.verdict is Verdict.REJECT:
      qa_suppressed.append(SuppressedComment(comment, CRITIC_REJECTED, v.reason))
    else:
      repair_pairs.append((comment, v))

  feedback = None
  if repair_pairs:
    feedback = "A previous draft of these comments failed QA. Regenerate ONLY these:\n" + "\n".join(
      f"- {c.file_path}:{c.line} «{c.title}»: {v.reason}"
      + (f" Repair: {v.repair_instruction}" if v.repair_instruction else "")
      for c, v in repair_pairs
    )

  detail["llm"] = {
    "accepted": len(accepted),
    "rejected": len(qa_suppressed),
    "repair": len(repair_pairs),
    "reasons": [v.reason for v in qa.verdicts][:10],
  }
  return {
    "qa_result": qa,
    "accepted_comments": accepted,
    "suppressed_comments": det_suppressed + qa_suppressed,
    "candidate_comments": [c for c, _ in repair_pairs],
    "repair_feedback": feedback,
    "needs_repair": bool(repair_pairs),
    "events": [{"node": "critic_qa", "detail": detail}],
  }


async def repair_generator_node(state: ReviewGraphState) -> dict:
  from app.llm.reviewer import generate_comments

  _, regenerated = await generate_comments(
    files=state["changed_files"],
    pr_title=state["pr_title"],
    pr_body=state["pr_body"],
    client=state["llm"],
    model=state["review_model"],
    contexts=state.get("retrieved_context", []),
    feedback=state["repair_feedback"],
    review_focus=state["route"].review_focus if state.get("route") else [],
  )
  retry = state.get("retry_count", 0) + 1
  return {
    "candidate_comments": regenerated,
    "retry_count": retry,
    "events": [
      {
        "node": "repair_generator",
        "detail": {"retry_count": retry, "regenerated": len(regenerated)},
      }
    ],
  }


async def publisher_node(state: ReviewGraphState) -> dict:
  """Builds the review payload; the worker POSTs it. Phase 2C markers make
  every artifact attributable to this run."""
  from app.agents.schemas import ReviewResult
  from app.github.formatting import format_comment_body, format_review_summary

  accepted = state.get("accepted_comments", [])
  result = ReviewResult(
    summary=state.get("summary", ""),
    comments=accepted,
    should_post_review=True,
    abstain_reason=None,
  )
  review_comments = [
    {
      "path": c.file_path,
      "line": c.line,
      "side": c.side,
      "body": format_comment_body(c, run_id=state["run_id"]),
    }
    for c in accepted
  ]
  return {
    "should_publish": True,
    "abstain_reason": None,
    "review_body": format_review_summary(result, run_id=state["run_id"]),
    "review_comments": review_comments,
    "events": [
      {
        "node": "publisher",
        "detail": {
          "comment_count": len(review_comments),
          "targets": [{"path": c["path"], "line": c["line"]} for c in review_comments],
        },
      }
    ],
  }


async def suppressor_node(state: ReviewGraphState) -> dict:
  """Finalizes abstention. Also suppresses candidates stranded by retry
  exhaustion — nothing the critic still flags may ever be published."""
  route = state.get("route")
  stranded = state.get("candidate_comments", [])
  newly_suppressed = [
    SuppressedComment(c, RETRY_EXHAUSTED, state.get("repair_feedback")) for c in stranded
  ]

  if route is not None and route.abstain:
    reason = route.reason or "route_abstained"
  else:
    reason = state.get("abstain_reason") or "all_comments_suppressed"

  return {
    "should_publish": False,
    "abstain_reason": reason,
    "suppressed_comments": newly_suppressed,
    "events": [
      {
        "node": "suppressor",
        "detail": {
          "abstain_reason": reason,
          "stranded_suppressed": len(newly_suppressed),
          "total_suppressed": len(state.get("suppressed_comments", [])) + len(newly_suppressed),
        },
      }
    ],
  }


def route_after_triage(state: ReviewGraphState) -> str:
  route = state.get("route")
  if route is None or route.abstain:
    return "suppressor"
  return "rag_retriever" if route.use_rag else "review_generator"


def route_after_qa(state: ReviewGraphState) -> str:
  """Exactly the spec policy:
  accepted -> publish; repair + retry_count < 2 -> repair;
  reject, or retry_count == 2 still not accepted -> suppress."""
  if state.get("needs_repair") and state.get("retry_count", 0) < MAX_RETRY_COUNT:
    return "repair_generator"
  if state.get("needs_repair"):
    return "suppressor"  # exhausted
  return "publisher" if state.get("accepted_comments") else "suppressor"


def build_graph() -> Any:
  builder = StateGraph(ReviewGraphState)
  builder.add_node("triage_router", triage_router_node)
  builder.add_node("rag_retriever", rag_retriever_node)
  builder.add_node("review_generator", review_generator_node)
  builder.add_node("critic_qa", critic_qa_node)
  builder.add_node("repair_generator", repair_generator_node)
  builder.add_node("publisher", publisher_node)
  builder.add_node("suppressor", suppressor_node)

  builder.set_entry_point("triage_router")
  builder.add_conditional_edges(
    "triage_router",
    route_after_triage,
    {
      "suppressor": "suppressor",
      "rag_retriever": "rag_retriever",
      "review_generator": "review_generator",
    },
  )
  builder.add_edge("rag_retriever", "review_generator")
  builder.add_edge("review_generator", "critic_qa")
  builder.add_conditional_edges(
    "critic_qa",
    route_after_qa,
    {
      "repair_generator": "repair_generator",
      "suppressor": "suppressor",
      "publisher": "publisher",
    },
  )
  builder.add_edge("repair_generator", "critic_qa")
  builder.add_edge("publisher", END)
  builder.add_edge("suppressor", END)
  return builder.compile()


@dataclass
class GraphRunOutput:
  accepted: list[ReviewComment]
  suppressed: list[SuppressedComment]
  should_publish: bool
  abstain_reason: str | None
  summary: str
  review_body: str
  review_comments: list[dict]
  events: list[GraphEvent]
  route: Any
  retry_count: int


async def run_review_graph(
  *,
  session: Any,
  llm: Any,
  snapshot_id: int | None,
  run_id: int,
  pr_number: int,
  commit_sha: str,
  pr_title: str,
  pr_body: str,
  diff: str,
  changed_files: list[Any],
  config_version: str,
  router_model: str,
  review_model: str,
  critic_model: str,
  embedding_model: str,
) -> GraphRunOutput:
  final = await build_graph().ainvoke(
    {
      "run_id": run_id,
      "pr_number": pr_number,
      "commit_sha": commit_sha,
      "pr_title": pr_title,
      "pr_body": pr_body,
      "config_version": config_version,
      "diff": diff,
      "changed_files": changed_files,
      "snapshot_id": snapshot_id,
      "ui_screenshot_url": None,
      "session": session,
      "llm": llm,
      "router_model": router_model,
      "review_model": review_model,
      "critic_model": critic_model,
      "embedding_model": embedding_model,
      "trace_id": uuid.uuid4().hex,
      "retry_count": 0,
      "accepted_comments": [],
      "suppressed_comments": [],
      "errors": [],
      "events": [],
    }
  )
  return GraphRunOutput(
    accepted=final.get("accepted_comments", []),
    suppressed=final.get("suppressed_comments", []),
    should_publish=final.get("should_publish", False),
    abstain_reason=final.get("abstain_reason"),
    summary=final.get("summary", ""),
    review_body=final.get("review_body", ""),
    review_comments=final.get("review_comments", []),
    events=final.get("events", []),
    route=final.get("route"),
    retry_count=final.get("retry_count", 0),
  )
