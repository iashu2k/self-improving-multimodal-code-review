from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from app.core.config import settings
from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
from app.db.session import get_session_maker
from app.github.app_auth import get_installation_token
from app.github.client import GitHubClient
from app.github.diff_parser import ChangedFile, parse_unified_diff
from app.github.formatting import format_comment_body, format_review_summary
from app.llm.openrouter_client import OpenRouterClient
from app.llm.reviewer import review_diff

logger = structlog.get_logger(__name__)

IGNORED_EXACT_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "Pipfile.lock",
}

IGNORED_SUFFIXES = (
    ".min.js",
    ".min.css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
)


def is_reviewable(changed_file: ChangedFile) -> bool:
    if changed_file.status == "deleted":
        return False
    name = changed_file.path.rsplit("/", 1)[-1]
    if name in IGNORED_EXACT_NAMES:
        return False
    return not changed_file.path.endswith(IGNORED_SUFFIXES)


async def run_pr_review(
    ctx: dict,
    *,
    installation_id: int,
    repository_owner: str,
    repository_name: str,
    pr_number: int,
    pr_title: str,
    pr_body: str,
    head_sha: str,
) -> dict:
    log = logger.bind(
        repo=f"{repository_owner}/{repository_name}",
        pr_number=pr_number,
        head_sha=head_sha[:8],
    )
    log.info("review_job_started")

    session_maker = get_session_maker()

    async with session_maker() as session:
        # One run row per identity. PUBLISHED is terminal (skip); any other
        # status (failed/abstained/stale-running) is reused and reset, so
        # retries continue the same run's history instead of inserting
        # duplicates that violate uq_review_run_identity.
        run = await session.scalar(
            select(ReviewRun).where(
                ReviewRun.repo_owner == repository_owner,
                ReviewRun.repo_name == repository_name,
                ReviewRun.pr_number == pr_number,
                ReviewRun.head_sha == head_sha,
                ReviewRun.config_version == settings.config_version,
            )
        )

        if run and run.status == RunStatus.PUBLISHED:
            log.info("review_skipped_duplicate", run_id=run.id)
            return {"status": "skipped_duplicate", "run_id": run.id}

        if run:
            log.info("review_run_resumed", run_id=run.id, previous_status=run.status)
            run.status = RunStatus.RUNNING
            run.abstain_reason = None
            run.error = None
            run.completed_at = None
        else:
            run = ReviewRun(
                repo_owner=repository_owner,
                repo_name=repository_name,
                pr_number=pr_number,
                head_sha=head_sha,
                config_version=settings.config_version,
                status=RunStatus.RUNNING,
            )
            session.add(run)

        await session.commit()
        await session.refresh(run)

        try:
            token = await get_installation_token(installation_id)
            github = GitHubClient(token)

            try:
                diff_text = await github.get_pr_diff(repository_owner, repository_name, pr_number)
                files = parse_unified_diff(diff_text)
                reviewable = [f for f in files if is_reviewable(f)]

                log.info(
                    "diff_fetched",
                    total_files=len(files),
                    reviewable_files=len(reviewable),
                )

                if not reviewable:
                    run.status = RunStatus.ABSTAINED
                    run.abstain_reason = "no_reviewable_files"
                    run.completed_at = datetime.now(UTC)
                    await session.commit()
                    return {"status": "abstained", "reason": "no_reviewable_files"}

                if not settings.openrouter_review_model:
                    raise RuntimeError("OPENROUTER_REVIEW_MODEL is not configured")

                llm = OpenRouterClient()
                try:
                    generated = await review_diff(
                        files=reviewable,
                        pr_title=pr_title,
                        pr_body=pr_body,
                        client=llm,
                        model=settings.openrouter_review_model,
                    )
                finally:
                    await llm.aclose()

                # Persist suppressed comments — they are eval gold later.
                for s in generated.suppressed:
                    session.add(
                        StoredReviewComment(
                            run_id=run.id,
                            file_path=s.comment.file_path,
                            line=s.comment.line,
                            severity=s.comment.severity.value,
                            category=s.comment.category.value,
                            title=s.comment.title,
                            body=s.comment.body,
                            suggested_fix=s.comment.suggested_fix,
                            confidence=s.comment.confidence,
                            status=CommentStatus.SUPPRESSED,
                            suppression_reason=s.reason,
                        )
                    )

                if not generated.result.should_post_review:
                    run.status = RunStatus.ABSTAINED
                    run.abstain_reason = generated.result.abstain_reason
                    run.completed_at = datetime.now(UTC)
                    await session.commit()
                    log.info("review_abstained", reason=run.abstain_reason)
                    return {"status": "abstained", "reason": run.abstain_reason}

                current_head_sha = await github.get_pr_head_sha(
                    repository_owner, repository_name, pr_number
                )

                comments = [
                    {
                        "path": c.file_path,
                        "line": c.line,
                        "side": c.side,
                        "body": format_comment_body(c),
                    }
                    for c in generated.accepted
                ]

                log.info(
                    "review_submitting",
                    commit_id=current_head_sha,
                    comments=[{"path": c["path"], "line": c["line"]} for c in comments],
                )

                review_response = await github.create_review(
                    repository_owner,
                    repository_name,
                    pr_number,
                    commit_id=current_head_sha,
                    body=format_review_summary(generated.result),
                    comments=comments,
                )

                for c in generated.accepted:
                    session.add(
                        StoredReviewComment(
                            run_id=run.id,
                            file_path=c.file_path,
                            line=c.line,
                            severity=c.severity.value,
                            category=c.category.value,
                            title=c.title,
                            body=c.body,
                            suggested_fix=c.suggested_fix,
                            confidence=c.confidence,
                            status=CommentStatus.POSTED,
                        )
                    )

                run.status = RunStatus.PUBLISHED
                run.github_review_id = review_response.get("id")
                run.completed_at = datetime.now(UTC)
                await session.commit()

                log.info(
                    "review_published",
                    review_id=run.github_review_id,
                    comment_count=len(comments),
                )
                return {"status": "published", "comments": len(comments)}

            finally:
                await github.aclose()

        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error = f"{type(exc).__name__}: {exc}"[:2000]
            run.completed_at = datetime.now(UTC)
            await session.commit()
            raise
