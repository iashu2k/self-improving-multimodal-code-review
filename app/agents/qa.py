"""Step 6: deterministic safety checks, run before any LLM critique.

Placement (file exists, line commentable) is delegated to the existing
validator — unchanged, still the hard gate. Everything below is content QA:
cheap heuristics that suppress bad candidates without spending critic tokens.
"""

import re

from app.agents.qa_schemas import (
    QA_DUPLICATE,
    QA_EMPTY_EVIDENCE,
    QA_FIX_TOO_LONG,
    QA_NO_RATIONALE,
    SuppressedComment,
)
from app.agents.schemas import ReviewComment
from app.github.diff_parser import ChangedFile

MAX_FIX_CHARS = 300
MIN_BODY_WORDS = 8
DUPLICATE_SIMILARITY = 0.8


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if (a | b) else 0.0


def check_comment_content(comment: ReviewComment) -> str | None:
    """Reject only *malformed* comments deterministically. Rationale quality
    is a semantic judgment — that's the critic's job (its verdicts carry
    grounded/actionable). Kept cheap: evidence present, body non-trivial,
    fix concise. Reason strings are a cross-layer contract — do not rename."""
    if not comment.evidence:
        return QA_EMPTY_EVIDENCE
    if len(comment.body.split()) < MIN_BODY_WORDS:
        return QA_NO_RATIONALE  # body too thin to carry a claim, let alone a rationale
    if comment.suggested_fix and len(comment.suggested_fix) > MAX_FIX_CHARS:
        return QA_FIX_TOO_LONG
    return None


def run_deterministic_qa(
    comments: list[ReviewComment], *, files: list[ChangedFile]
) -> tuple[list[ReviewComment], list[SuppressedComment]]:
    # 1. Placement gate — the existing validator, unchanged.
    #    INTEGRATION POINT: adjust to its real signature/suppressed shape.
    from app.agents.schemas import ReviewResult
    from app.agents.validator import validate_review_comments

    probe = ReviewResult(
        summary="",
        comments=comments,
        should_post_review=bool(comments),
        abstain_reason=None,
    )
    validation = validate_review_comments(result=probe, files=files)
    placed = validation.accepted_comments
    suppressed = [
        SuppressedComment(comment=s.comment, reason=s.reason)
        for s in validation.suppressed_comments
    ]

    # 2. Content checks + near-duplicate similarity (validator catches exact
    #    dupes; Jaccard catches the same claim reworded on another line).
    survivors: list[ReviewComment] = []
    for comment in placed:
        reason = check_comment_content(comment)
        if reason:
            suppressed.append(SuppressedComment(comment, reason))
        elif any(
            _jaccard(_tokens(comment.body), _tokens(s.body)) >= DUPLICATE_SIMILARITY
            for s in survivors
        ):
            suppressed.append(SuppressedComment(comment, QA_DUPLICATE))
        else:
            survivors.append(comment)
    return survivors, suppressed
