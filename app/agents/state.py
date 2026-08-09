"""Phase 4 graph state. Reducer fields (operator.add) accumulate across
repair rounds; everything else is last-write-wins.

No LangGraph checkpointer: Postgres (review_runs + review_run_events) is the
durable state — so non-serializable plumbing (session, llm) is safe here.
"""

import operator
from typing import Annotated, Any, TypedDict

from app.agents.qa_schemas import (
    QAResult,
    RouteDecision,
    SuppressedComment,
    VisionObservation,
)
from app.agents.schemas import ReviewComment
from app.github.diff_parser import ChangedFile
from app.ingestion.retriever import RetrievedContext


class GraphEvent(TypedDict):
    node: str
    detail: dict[str, Any]


class ReviewGraphState(TypedDict, total=False):
    # --- identity (set by worker before ainvoke) ---
    run_id: int
    pr_number: int
    commit_sha: str
    pr_title: str
    pr_body: str
    config_version: str
    diff: str
    changed_files: list[ChangedFile]
    snapshot_id: int | None
    ui_screenshot_url: str | None  # Phase 5

    # --- routing ---
    route: RouteDecision | None

    # --- evidence ---
    retrieved_context: list[RetrievedContext]
    vision_observations: list[VisionObservation]  # Phase 5; always [] for now

    # --- generation + QA loop ---
    candidate_comments: list[ReviewComment]
    qa_result: QAResult | None
    repair_feedback: str | None
    needs_repair: bool
    retry_count: int

    # --- outputs ---
    accepted_comments: Annotated[list[ReviewComment], operator.add]
    suppressed_comments: Annotated[list[SuppressedComment], operator.add]

    # --- publish payload (built by publisher node, POSTed by worker) ---
    summary: str
    should_publish: bool
    abstain_reason: str | None
    review_body: str
    review_comments: list[dict]

    # --- observability ---
    trace_id: str | None
    errors: Annotated[list[str], operator.add]
    events: Annotated[list[GraphEvent], operator.add]

    # --- plumbing (not persisted by LangGraph) ---
    session: Any
    llm: Any
    router_model: str
    review_model: str
    critic_model: str
    embedding_model: str
