import structlog

from app.agents.schemas import ReviewResult
from app.core.config import settings
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
            log.info("review_skipped", reason="no_reviewable_files")
            return {"status": "skipped", "reason": "no_reviewable_files"}

        if not settings.openrouter_review_model:
            raise RuntimeError("OPENROUTER_REVIEW_MODEL is not configured")

        llm = OpenRouterClient()
        try:
            result: ReviewResult = await review_diff(
                files=reviewable,
                pr_title=pr_title,
                pr_body=pr_body,
                client=llm,
                model=settings.openrouter_review_model,
            )
        finally:
            await llm.aclose()

        if not result.should_post_review:
            log.info("review_abstained", reason=result.abstain_reason)
            return {"status": "abstained", "reason": result.abstain_reason}

        comments = [
            {
                "path": c.file_path,
                "line": c.line,
                "side": c.side,
                "body": format_comment_body(c),
            }
            for c in result.comments
        ]
        log.info(
            "review_submitting",
            commit_id=head_sha,
            comments=[{"path": c["path"], "line": c["line"], "side": c["side"]} for c in comments],
        )
        review_response = await github.create_review(
            repository_owner,
            repository_name,
            pr_number,
            commit_id=head_sha,
            body=format_review_summary(result),
            comments=comments,
        )

        log.info(
            "review_published",
            review_id=review_response.get("id"),
            comment_count=len(comments),
        )
        return {"status": "published", "comments": len(comments)}

    finally:
        await github.aclose()
