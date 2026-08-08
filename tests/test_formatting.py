from app.agents.schemas import ReviewCategory, ReviewComment, Severity
from app.github.formatting import format_comment_body


def test_format_comment_body_includes_severity_and_fix() -> None:
    comment = ReviewComment(
        file_path="calc.py",
        line=3,
        side="RIGHT",
        severity=Severity.HIGH,
        category=ReviewCategory.BUG_RISK,
        title="Silent float truncation",
        body="int() truncates the division result.",
        evidence=["return int(result)"],
        suggested_fix="Return result unchanged or use round().",
        confidence=0.9,
    )

    body = format_comment_body(comment)

    assert "HIGH" in body
    assert "bug risk" in body
    assert "Silent float truncation" in body
    assert "Suggested fix" in body
