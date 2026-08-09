"""Step 6: LLM critic. Judges content the deterministic layer can't:
groundedness against the actual diff/context, actionability, duplication.
Advisory-but-enforced — its verdicts decide accept/repair/reject."""

from app.agents.qa_schemas import QAResult
from app.agents.schemas import ReviewComment
from app.github.diff_parser import ChangedFile
from app.ingestion.retriever import RetrievedContext
from app.llm.openrouter_client import OpenRouterClient

MAX_DIFF_CHARS = 8000
MAX_CONTEXT_CHARS = 400

CRITIC_SYSTEM_PROMPT = """\
You are the critic in a code-review pipeline. A deterministic validator has \
already guaranteed each candidate points at a real, commentable diff line. \
You judge what it cannot. For EACH candidate, output one verdict:

- accept: every claim is supported by the diff or retrieved context, the \
issue is real (not speculative), and the author would know what to change.
- repair: the issue is real but the comment is materially wrong in a \
fixable way (wrong severity, one unsupported claim among supported ones, \
misleading title, broken suggested fix). repair_instruction is REQUIRED — \
state exactly what to change and what claim to limit it to.
- reject: the claim is fabricated or unsupported by diff/context, the \
issue is speculative ("might/could" with no evidence), a style nitpick \
not backed by conventions visible in context, or a duplicate of another \
candidate (reject the later one).

Set grounded/actionable/duplicate/policy_safe honestly per candidate. \
Groundedness is judged ONLY against the diff and context shown to you. \
Borderline: prefer repair over reject, reject over accept — silence is \
cheaper than a wrong comment. One verdict per candidate index; every \
input index must appear exactly once."""


def _render_diff_excerpt(files: list[ChangedFile]) -> str:
    parts = []
    for f in files:
        parts.append(f"=== {f.path} ({f.status}) ===")
        for hunk in f.hunks:
            for line in hunk.lines:
                if line.kind == "add":
                    parts.append(f"+{line.new_lineno}: {line.content}")
                elif line.kind == "del":
                    parts.append(f"-: {line.content}")
    return "\n".join(parts)[:MAX_DIFF_CHARS]


def _render_contexts(contexts: list[RetrievedContext]) -> str:
    if not contexts:
        return "(no retrieved context)"
    return "\n\n".join(f"--- {c.file_path} ---\n{c.content[:MAX_CONTEXT_CHARS]}" for c in contexts)


def _render_candidates(comments: list[ReviewComment]) -> str:
    return "\n\n".join(
        f"[index {i}] {c.file_path}:{c.line} ({c.side})\n"
        f"  severity={c.severity.value} category={c.category.value} "
        f"confidence={c.confidence}\n"
        f"  title: {c.title}\n  body: {c.body}\n"
        f"  evidence: {c.evidence}\n"
        f"  suggested_fix: {c.suggested_fix or '(none)'}"
        for i, c in enumerate(comments)
    )


async def critique_candidates(
    *,
    client: OpenRouterClient,
    model: str,
    files: list[ChangedFile],
    comments: list[ReviewComment],
    contexts: list[RetrievedContext],
) -> QAResult:
    user_prompt = (
        "## Diff under review\n\n"
        + _render_diff_excerpt(files)
        + "\n\n## Retrieved repository context (ground truth for citations)\n\n"
        + _render_contexts(contexts)
        + "\n\n## Candidate comments to judge\n\n"
        + _render_candidates(comments)
    )
    response = await client.chat_structured(
        model=model,
        schema_name="qa_result",
        json_schema=QAResult.model_json_schema(),
        messages=[
            {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    return QAResult.model_validate(response.content)
