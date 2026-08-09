"""Step 3: triage router.

Deterministic rules run first and can settle the route with no LLM spend.
The router model is consulted only for nuanced strategy (risk level, review
focus) on diffs that pass the deterministic gate — and even then, policy
overrides its output (the model advises, policy decides).
"""

from app.agents.qa_schemas import RiskLevel, RouteDecision
from app.core.config import settings
from app.github.diff_parser import ChangedFile
from app.llm.router import route_pr

DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc")
FRONTEND_SUFFIXES = (".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss")
SECURITY_PATH_MARKERS = (
    "auth",
    "security",
    "crypto",
    "permission",
    "payment",
    "billing",
    "token",
    "secret",
    "session",
)


def added_line_count(files: list[ChangedFile]) -> int:
    return sum(1 for f in files for h in f.hunks for line in h.lines if line.kind == "add")


def is_docs_only(files: list[ChangedFile]) -> bool:
    return all(f.path.lower().endswith(DOC_SUFFIXES) for f in files)


def security_hits(files: list[ChangedFile]) -> list[str]:
    return sorted({m for f in files for m in SECURITY_PATH_MARKERS if m in f.path.lower()})


def frontend_changed(files: list[ChangedFile]) -> bool:
    return any(f.path.lower().endswith(FRONTEND_SUFFIXES) for f in files)


async def decide_route(
    *,
    client,
    model: str,
    files: list[ChangedFile],
    pr_title: str,
    pr_body: str,
) -> RouteDecision:
    # --- deterministic skip rules (spec order) ---
    if not files:
        return RouteDecision(
            risk_level=RiskLevel.LOW,
            use_rag=False,
            abstain=True,
            reason="no_source_changes",
        )
    if is_docs_only(files):
        return RouteDecision(
            risk_level=RiskLevel.LOW,
            use_rag=False,
            abstain=True,
            reason="docs_only",
        )
    if len(files) > settings.review_max_files or (
        added_line_count(files) > settings.review_max_added_lines
    ):
        # manual mode for now; sampled review is a later refinement
        return RouteDecision(
            risk_level=RiskLevel.LOW,
            abstain=True,
            reason="pr_too_large",
        )

    # --- nuanced strategy: the router model ---

    route = await route_pr(
        client=client,
        model=model,
        files=files,
        pr_title=pr_title,
        pr_body=pr_body,
    )

    # --- deterministic overrides on model output ---
    focus = set(route.review_focus)
    if security_hits(files):
        focus.add("security")  # security-sensitive paths force the focus
    updates: dict = {
        "use_vision": False,  # hard-off until Phase 5, no matter what the model says
        "review_focus": sorted(focus),
    }
    return route.model_copy(update=updates)
