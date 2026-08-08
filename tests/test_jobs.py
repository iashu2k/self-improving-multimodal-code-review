from unittest.mock import AsyncMock

import pytest

from app.agents.schemas import ReviewCategory, ReviewComment, ReviewResult, Severity
from app.workers import jobs

SAMPLE_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@ def divide(a, b):
-    return a / b
+    result = a / b
+    return int(result)
"""


@pytest.fixture(autouse=True)
def clear_cache() -> None:
    jobs.clear_token_cache = getattr(jobs, "clear_token_cache", None)


@pytest.mark.asyncio
async def test_run_pr_review_publishes_review(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "get_installation_token", AsyncMock(return_value="tok"))

    fake_github = AsyncMock()
    fake_github.get_pr_diff.return_value = SAMPLE_DIFF
    fake_github.create_review.return_value = {"id": 99}
    monkeypatch.setattr(jobs, "GitHubClient", lambda token: fake_github)

    review_result = ReviewResult(
        summary="Truncation bug.",
        comments=[
            ReviewComment(
                file_path="calc.py",
                line=3,
                side="RIGHT",
                severity=Severity.HIGH,
                category=ReviewCategory.BUG_RISK,
                title="Float truncation",
                body="int() truncates the quotient.",
                evidence=["return int(result)"],
                suggested_fix="Return result directly.",
                confidence=0.95,
            )
        ],
        should_post_review=True,
    )
    monkeypatch.setattr(jobs, "review_diff", AsyncMock(return_value=review_result))
    monkeypatch.setattr(jobs, "OpenRouterClient", lambda: AsyncMock())

    outcome = await jobs.run_pr_review(
        {},
        installation_id=1,
        repository_owner="owner",
        repository_name="repo",
        pr_number=1,
        pr_title="t",
        pr_body="",
        head_sha="abc123",
    )

    assert outcome["status"] == "published"
    call = fake_github.create_review.call_args
    assert call.kwargs["commit_id"] == "abc123"
    assert call.kwargs["comments"][0]["line"] == 3
