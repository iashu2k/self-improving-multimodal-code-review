import json

import structlog

from app.agents.schemas import ReviewResult
from app.agents.validator import validate_review_comments
from app.github.diff_parser import ChangedFile
from app.llm.openrouter_client import OpenRouterClient
from app.llm.prompts.review import SYSTEM_PROMPT

MAX_DIFF_CHARS = 60_000

logger = structlog.get_logger(__name__)


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


async def review_diff(
    *,
    files: list[ChangedFile],
    pr_title: str,
    pr_body: str,
    client: OpenRouterClient,
    model: str,
) -> ReviewResult:
    rendered = render_diff_for_prompt(files)
    if len(rendered) > MAX_DIFF_CHARS:
        rendered = rendered[:MAX_DIFF_CHARS] + "\n[DIFF TRUNCATED]"

    commentable_lines = {f.path: sorted(f.commentable_lines) for f in files if f.commentable_lines}

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

    validation = validate_review_comments(
        result=raw_result,
        files=files,
    )

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
        return ReviewResult(
            summary=raw_result.summary,
            comments=validation.accepted_comments,
            should_post_review=True,
            abstain_reason=None,
        )

    reason = raw_result.abstain_reason
    if validation.suppressed_comments and reason is None:
        reason = "All generated comments failed deterministic validation."

    return ReviewResult(
        summary=raw_result.summary,
        comments=[],
        should_post_review=False,
        abstain_reason=reason,
    )
