import json
from dataclasses import dataclass

import structlog

from app.agents.schemas import ReviewComment, ReviewResult
from app.agents.validator import SuppressedComment, validate_review_comments
from app.github.diff_parser import ChangedFile
from app.ingestion.retriever import RetrievedContext
from app.llm.openrouter_client import OpenRouterClient
from app.llm.prompts.review import SYSTEM_PROMPT

logger = structlog.get_logger(__name__)

MAX_DIFF_CHARS = 60_000
MAX_CONTEXT_CONTENT_CHARS = 1500


@dataclass
class GeneratedReview:
    result: ReviewResult
    accepted: list[ReviewComment]
    suppressed: list[SuppressedComment]


def render_diff_for_prompt(files: list[ChangedFile]) -> str:
    blocks: list[str] = []
    for f in files:
        lines = [f"FILE: {f.path} (status={f.status})"]
        for hunk in f.hunks:
            header = f"@@ -{hunk.old_start},{hunk.old_count} +{hunk.new_start},{hunk.new_count} @@"
            lines.append(header)
            for line in hunk.lines:
                if line.kind == "add":
                    lines.append(f"+ [line {line.new_lineno}] {line.content}")
                elif line.kind == "del":
                    lines.append(f"- {line.content}")
                else:
                    lines.append(f"  {line.content}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_contexts_for_prompt(contexts: list[RetrievedContext]) -> str:
    blocks = []
    for c in contexts:
        symbol = c.symbol or "(module)"
        content = c.content[:MAX_CONTEXT_CONTENT_CHARS]
        blocks.append(
            f"CONTEXT: {c.file_path}::{symbol} "
            f"({c.chunk_type}, lines {c.start_line}-{c.end_line})\n{content}"
        )
    return "\n\n".join(blocks)


async def review_diff(
    *,
    files: list[ChangedFile],
    pr_title: str,
    pr_body: str,
    client: OpenRouterClient,
    model: str,
    contexts: list[RetrievedContext] | None = None,
) -> GeneratedReview:
    rendered = render_diff_for_prompt(files)
    if len(rendered) > MAX_DIFF_CHARS:
        rendered = rendered[:MAX_DIFF_CHARS] + "\n[DIFF TRUNCATED]"

    commentable_lines = {f.path: sorted(f.right_side_lines) for f in files if f.right_side_lines}

    context_block = ""
    if contexts:
        context_block = (
            "\n\nRelevant repository context retrieved for this PR "
            "(use it as evidence and cite the file and symbol when it supports "
            "a finding; do NOT comment on lines in context files — comments "
            "must target the diff):\n"
            f"{render_contexts_for_prompt(contexts)}"
        )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"PR title: {pr_title}\nPR description: {pr_body}\n\n"
                f"Commentable RIGHT-side line numbers per file "
                f"(every comment's file_path and line MUST come from this map):\n"
                f"{json.dumps(commentable_lines, indent=2)}\n\n"
                f"Review this diff:\n\n{rendered}"
                f"{context_block}"
            ),
        },
    ]

    response = await client.chat_structured(
        model=model,
        schema_name="review_result",
        json_schema=ReviewResult.model_json_schema(),
        messages=messages,
    )
    raw_result = ReviewResult.model_validate(response.content)

    validation = validate_review_comments(result=raw_result, files=files)

    if validation.suppressed_comments:
        logger.warning(
            "comments_suppressed",
            suppressed=[
                {
                    "file_path": s.comment.file_path,
                    "line": s.comment.line,
                    "reason": s.reason,
                    "title": s.comment.title,
                }
                for s in validation.suppressed_comments
            ],
        )

    if validation.accepted_comments:
        result = ReviewResult(
            summary=raw_result.summary,
            comments=validation.accepted_comments,
            should_post_review=True,
            abstain_reason=None,
        )
    else:
        reason = raw_result.abstain_reason
        if validation.suppressed_comments and reason is None:
            reason = "All generated comments failed deterministic validation."
        result = ReviewResult(
            summary=raw_result.summary,
            comments=[],
            should_post_review=False,
            abstain_reason=reason,
        )

    return GeneratedReview(
        result=result,
        accepted=validation.accepted_comments,
        suppressed=validation.suppressed_comments,
    )
