from app.agents.schemas import ReviewComment, ReviewResult

SEVERITY_EMOJI = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
}


def format_comment_body(comment: ReviewComment) -> str:
    emoji = SEVERITY_EMOJI.get(comment.severity.value, "⚪")
    category = comment.category.value.replace("_", " ")

    parts = [
        f"{emoji} **[{comment.severity.value.upper()} · {category}]** {comment.title}",
        "",
        comment.body,
    ]

    if comment.suggested_fix:
        parts.extend(["", f"**Suggested fix:** {comment.suggested_fix}"])

    return "\n".join(parts)


def format_review_summary(result: ReviewResult) -> str:
    count = len(result.comments)
    plural = "s" if count != 1 else ""
    return (
        f"### 🤖 Self-Improving Multimodal Code Review\n\n"
        f"{result.summary}\n\n"
        f"Posted {count} inline comment{plural}. "
        f"Feedback on these comments helps tune future reviews."
    )
