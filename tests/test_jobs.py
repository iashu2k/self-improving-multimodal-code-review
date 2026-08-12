from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.agents.graph import GraphRunOutput
from app.agents.schemas import (
  ReviewCategory,
  ReviewComment,
  Severity,
)
from app.agents.validator import SuppressedComment
from app.core.config import settings
from app.db.models.review import (
  CommentStatus,
  ReviewRun,
  ReviewRunEvent,
  RunStatus,
  StoredReviewComment,
)
from app.workers import jobs

SAMPLE_DIFF = """diff --git a/calc.py b/calc.py
--- a/calc.py
+++ b/calc.py
@@ -1,2 +1,3 @@ def divide(a, b):
-    return a / b
+    result = a / b
+    return int(result)
"""


LOCKFILE_ONLY_DIFF = """diff --git a/uv.lock b/uv.lock
--- a/uv.lock
+++ b/uv.lock
@@ -1,2 +1,2 @@
-version = "1.0.0"
+version = "1.0.1"
"""


def make_review_comment() -> ReviewComment:
  return ReviewComment(
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


def make_graph_output(*, should_post: bool = True) -> GraphRunOutput:
  """What run_review_graph returns on the two terminal paths. The graph's
  own behavior is covered in tests/test_run_graph.py — here we only need
  the worker to consume the output shape correctly."""
  comment = make_review_comment()
  if should_post:
    return GraphRunOutput(
      accepted=[comment],
      suppressed=[],
      should_publish=True,
      abstain_reason=None,
      summary="Truncation bug.",
      review_body="### 🤖 review summary <!-- review-forge marker -->",
      review_comments=[
        {
          "path": "calc.py",
          "line": 3,
          "side": "RIGHT",
          "body": '🔴 body <!-- review-forge {"run_id":1} -->',
        }
      ],
      events=[
        {"node": "triage_router", "detail": {"proceed": True}},
        {"node": "publisher", "detail": {"comment_count": 1}},
      ],
      route=None,
      retry_count=0,
    )
  return GraphRunOutput(
    accepted=[],
    suppressed=[
      SuppressedComment(
        comment=make_review_comment(),
        reason="line_not_in_diff",
      )
    ],
    should_publish=False,
    abstain_reason="all_comments_suppressed",
    summary="",
    review_body="",
    review_comments=[],
    events=[
      {"node": "triage_router", "detail": {"proceed": True}},
      {"node": "suppressor", "detail": {"abstain_reason": "all_comments_suppressed"}},
    ],
    route=None,
    retry_count=0,
  )


def install_fakes(
  monkeypatch: pytest.MonkeyPatch,
  session_maker,
  *,
  diff_text: str = SAMPLE_DIFF,
  graph_output: GraphRunOutput | None = None,
) -> AsyncMock:
  """Route all external boundaries (GitHub, OpenRouter, DB, graph) to fakes."""
  monkeypatch.setattr(jobs, "get_installation_token", AsyncMock(return_value="tok"))

  fake_github = AsyncMock()
  fake_github.get_pr_diff.return_value = diff_text
  fake_github.get_pr_head_sha.return_value = "current-head-sha"
  fake_github.create_review.return_value = {"id": 99}
  monkeypatch.setattr(jobs, "GitHubClient", lambda token: fake_github)

  # Phase 4 seam: the worker calls the graph, not review_diff directly
  monkeypatch.setattr(
    jobs,
    "run_review_graph",
    AsyncMock(return_value=graph_output or make_graph_output()),
  )
  monkeypatch.setattr(jobs, "OpenRouterClient", lambda: AsyncMock())
  monkeypatch.setattr(jobs, "get_session_maker", lambda: session_maker)

  # Indexing stays in the worker — stubbed, unit-tested separately
  fake_snapshot = SimpleNamespace(id=1, status="indexed")
  monkeypatch.setattr(jobs, "get_or_create_snapshot", AsyncMock(return_value=fake_snapshot))
  monkeypatch.setattr(jobs, "index_snapshot", AsyncMock())

  return fake_github


JOB_KWARGS = {
  "installation_id": 1,
  "repository_owner": "owner",
  "repository_name": "repo",
  "pr_number": 1,
  "pr_title": "t",
  "pr_body": "",
  "head_sha": "abc123",
}


@pytest.mark.asyncio
async def test_run_pr_review_publishes_review(
  monkeypatch: pytest.MonkeyPatch, session_maker
) -> None:
  fake_github = install_fakes(monkeypatch, session_maker)

  outcome = await jobs.run_pr_review({}, **JOB_KWARGS)

  assert outcome["status"] == "published"
  assert outcome["comments"] == 1

  call = fake_github.create_review.call_args
  assert call.kwargs["commit_id"] == "current-head-sha"
  assert call.kwargs["body"] == "### 🤖 review summary <!-- review-forge marker -->"
  assert call.kwargs["comments"][0]["line"] == 3
  assert call.kwargs["comments"][0]["path"] == "calc.py"

  async with session_maker() as session:
    run = await session.scalar(select(ReviewRun))
    assert run is not None
    assert run.status == RunStatus.PUBLISHED
    assert run.github_review_id == 99
    assert run.config_version == settings.config_version

    comments = (await session.scalars(select(StoredReviewComment))).all()
    assert len(comments) == 1
    assert comments[0].status == CommentStatus.POSTED
    assert comments[0].line == 3


@pytest.mark.asyncio
async def test_run_pr_review_persists_graph_events(
  monkeypatch: pytest.MonkeyPatch, session_maker
) -> None:
  install_fakes(monkeypatch, session_maker)

  outcome = await jobs.run_pr_review({}, **JOB_KWARGS)
  assert outcome["status"] == "published"

  async with session_maker() as session:
    run = await session.scalar(select(ReviewRun))
    events = (
      await session.scalars(select(ReviewRunEvent).where(ReviewRunEvent.run_id == run.id))
    ).all()
    assert [e.node for e in events] == ["triage_router", "publisher"]
    assert events[1].detail == {"comment_count": 1}


@pytest.mark.asyncio
async def test_run_pr_review_skips_when_already_published(
  monkeypatch: pytest.MonkeyPatch, session_maker
) -> None:
  install_fakes(monkeypatch, session_maker)

  async with session_maker() as session:
    session.add(
      ReviewRun(
        repo_owner="owner",
        repo_name="repo",
        pr_number=1,
        head_sha="abc123",
        config_version=settings.config_version,
        status=RunStatus.PUBLISHED,
      )
    )
    await session.commit()

  outcome = await jobs.run_pr_review({}, **JOB_KWARGS)

  assert outcome["status"] == "skipped_duplicate"


@pytest.mark.asyncio
async def test_run_pr_review_abstains_and_persists_suppressions(
  monkeypatch: pytest.MonkeyPatch, session_maker
) -> None:
  fake_github = install_fakes(
    monkeypatch,
    session_maker,
    graph_output=make_graph_output(should_post=False),
  )

  outcome = await jobs.run_pr_review({}, **JOB_KWARGS)

  assert outcome["status"] == "abstained"
  assert outcome["reason"] == "all_comments_suppressed"
  fake_github.create_review.assert_not_called()

  async with session_maker() as session:
    run = await session.scalar(select(ReviewRun))
    assert run is not None
    assert run.status == RunStatus.ABSTAINED
    assert run.abstain_reason == "all_comments_suppressed"

    comments = (await session.scalars(select(StoredReviewComment))).all()
    assert len(comments) == 1
    assert comments[0].status == CommentStatus.SUPPRESSED
    assert comments[0].suppression_reason == "line_not_in_diff"


@pytest.mark.asyncio
async def test_run_pr_review_abstains_when_no_reviewable_files(
  monkeypatch: pytest.MonkeyPatch, session_maker
) -> None:
  install_fakes(monkeypatch, session_maker, diff_text=LOCKFILE_ONLY_DIFF)

  outcome = await jobs.run_pr_review({}, **JOB_KWARGS)

  assert outcome["status"] == "abstained"
  assert outcome["reason"] == "no_reviewable_files"

  async with session_maker() as session:
    run = await session.scalar(select(ReviewRun))
    assert run is not None
    assert run.status == RunStatus.ABSTAINED
    assert run.abstain_reason == "no_reviewable_files"
