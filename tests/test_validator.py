from app.agents.schemas import ReviewCategory, ReviewComment, ReviewResult, Severity
from app.agents.validator import validate_review_comments
from app.github.diff_parser import parse_unified_diff
from tests.test_diff_parser import SAMPLE_DIFF


def make_comment(
    *,
    file_path: str = "app/auth.py",
    line: int = 11,
    title: str = "Example issue",
) -> ReviewComment:
    return ReviewComment(
        file_path=file_path,
        line=line,
        side="RIGHT",
        severity=Severity.HIGH,
        category=ReviewCategory.SECURITY,
        title=title,
        body="This is a concrete, actionable problem.",
        evidence=["if user.is_admin:"],
        suggested_fix="Require password validation.",
        confidence=0.9,
    )


def test_validator_accepts_comment_on_added_line() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    result = ReviewResult(
        summary="Review summary",
        comments=[make_comment(line=11)],
        should_post_review=True,
    )

    validation = validate_review_comments(result=result, files=files)

    assert len(validation.accepted_comments) == 1
    assert validation.suppressed_comments == []
    assert validation.should_post_review is True


def test_validator_accepts_context_line_for_deletion_findings() -> None:
    """Context lines are legal anchors for deletion-driven findings."""
    files = parse_unified_diff(SAMPLE_DIFF)
    result = ReviewResult(
        summary="Review summary",
        comments=[make_comment(line=10)],  # context line in SAMPLE_DIFF
        should_post_review=True,
    )

    validation = validate_review_comments(result=result, files=files)

    assert len(validation.accepted_comments) == 1


def test_validator_rejects_line_not_in_diff() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    result = ReviewResult(
        summary="Review summary",
        comments=[make_comment(line=99)],
        should_post_review=True,
    )

    validation = validate_review_comments(result=result, files=files)

    assert validation.accepted_comments == []
    assert validation.suppressed_comments[0].reason == "line_not_in_diff"


def test_validator_rejects_unknown_file() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    result = ReviewResult(
        summary="Review summary",
        comments=[make_comment(file_path="missing.py", line=11)],
        should_post_review=True,
    )

    validation = validate_review_comments(result=result, files=files)

    assert validation.accepted_comments == []
    assert validation.suppressed_comments[0].reason == "file_not_present_in_diff"


def test_validator_rejects_same_line_duplicate() -> None:
    files = parse_unified_diff(SAMPLE_DIFF)
    result = ReviewResult(
        summary="Review summary",
        comments=[
            make_comment(line=11, title="First issue"),
            make_comment(line=11, title="Second issue"),
        ],
        should_post_review=True,
    )

    validation = validate_review_comments(result=result, files=files)

    assert len(validation.accepted_comments) == 1
    assert len(validation.suppressed_comments) == 1
    assert validation.suppressed_comments[0].reason == "duplicate_comment_location"
