import json

from app.agents.schemas import ReviewComment, ReviewResult

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}

COMMENT_FEEDBACK_PROMPT = "👍 / 👎 on this comment tunes future reviews."
SUMMARY_FEEDBACK_PROMPT = "👍 / 👎 on this review tunes future reviews."


def marker_payload(payload: dict) -> str:
    """Hidden identity marker. HTML comments survive GitHub's Markdown
    rendering untouched (front-matter and footnote tricks don't reliably),
    so this is invisible in the rendered review but parseable via the API."""
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return f"<!-- review-forge {encoded} -->"


def format_comment_body(comment: ReviewComment, *, run_id: int | None = None) -> str:
    emoji = SEVERITY_EMOJI.get(comment.severity.value, "⚪")
    category = comment.category.value.replace("_", " ")

    parts = [
        f"{emoji} **[{comment.severity.value.upper()} · {category}]** {comment.title}",
        "",
        comment.body,
    ]

    if comment.suggested_fix:
        parts.extend(["", f"**Suggested fix:** {comment.suggested_fix}"])

    if run_id is not None:
        marker = marker_payload({"run_id": run_id, "file": comment.file_path, "line": comment.line})
        parts.extend(["", "---", f"{COMMENT_FEEDBACK_PROMPT} {marker}"])

    return "\n".join(parts)


def format_review_summary(result: ReviewResult, *, run_id: int | None = None) -> str:
    count = len(result.comments)
    plural = "s" if count != 1 else ""
    parts = [
        "### 🤖 Self-Improving Multimodal Code Review",
        "",
        result.summary,
        "",
        f"Posted {count} inline comment{plural}. "
        "Feedback on these comments helps tune future reviews.",
    ]

    if run_id is not None:
        marker = marker_payload({"run_id": run_id, "kind": "summary"})
        parts.extend(["", "---", f"{SUMMARY_FEEDBACK_PROMPT} {marker}"])

    return "\n".join(parts)
