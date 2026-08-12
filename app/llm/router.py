"""Step 3: router model call. Strategy only — this model never sees enough
to make findings, and its output is post-processed by deterministic policy."""

from app.agents.qa_schemas import RouteDecision
from app.github.diff_parser import ChangedFile, reviewable_files  # CHANGED
from app.llm.openrouter_client import OpenRouterClient

ROUTER_SYSTEM_PROMPT = """\
You are the triage router in a code-review pipeline. You decide review \
STRATEGY, not findings. Given PR metadata and diff statistics, output:

- risk_level: low | medium | high (high = behavior-changing logic in core paths)
- review_focus: subset of ["correctness", "security", "performance", \
"reliability", "api-contract"] — what the reviewer should concentrate on
- use_rag: true when understanding the change requires code outside the \
diff (calls into other modules, overrides behavior, changes contracts)
- abstain: true only when the diff is clearly trivial (typos, comments, \
formatting-only)
- reason: one sentence justifying the strategy

Never set use_vision (handled elsewhere). When unsure, abstain=false."""


def _render_stats(files: list[ChangedFile]) -> str:
  lines = []
  for f in reviewable_files(files):  # CHANGED: deleted files are triage noise
    added = sum(1 for h in f.hunks for line in h.lines if line.kind == "add")
    deleted = sum(1 for h in f.hunks for line in h.lines if line.kind == "del")
    lines.append(f"{f.path} ({f.status}): +{added}/-{deleted}")
  return "\n".join(lines)


async def route_pr(
  *,
  client: OpenRouterClient,
  model: str,
  files: list[ChangedFile],
  pr_title: str,
  pr_body: str,
) -> RouteDecision:
  user_prompt = (
    f"PR title: {pr_title}\n"
    f"PR body: {pr_body or '(empty)'}\n\n"
    f"Changed files:\n{_render_stats(files)}"
  )
  # CHANGED: removed stale scaffolding NOTE — the call below already matches
  # reviewer.py's chat_structured signature exactly.
  response = await client.chat_structured(
    model=model,
    schema_name="route_decision",
    json_schema=RouteDecision.model_json_schema(),
    messages=[
      {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
      {"role": "user", "content": user_prompt},
    ],
  )
  return RouteDecision.model_validate(response.content)
