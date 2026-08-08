from typing import Annotated

import structlog
from arq import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_arq_pool
from app.core.config import settings
from app.db.models.review import ReviewRun, RunStatus
from app.db.models.webhook import WebhookEvent
from app.db.session import get_db
from app.github.webhook_verifier import WebhookVerificationError, verify_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)

REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


async def _enqueue_review(
    arq_pool: ArqRedis,
    *,
    payload: dict,
    repository: str,
    pr: dict,
    pr_number: int,
    delivery_id: str,
    is_retry: bool = False,
) -> None:
    owner, repo_name = repository.split("/", 1)
    head_sha = (pr.get("head") or {}).get("sha") or ""

    # Commit-scoped dedup key for normal deliveries; retries get a fresh key
    # because the failed ARQ job record would otherwise block re-enqueue.
    job_id = f"review-{repository}-{pr_number}-{head_sha[:8]}"
    if is_retry:
        job_id = f"{job_id}-retry-{delivery_id}"

    await arq_pool.enqueue_job(
        "run_pr_review",
        installation_id=(payload.get("installation") or {}).get("id"),
        repository_owner=owner,
        repository_name=repo_name,
        pr_number=pr_number,
        pr_title=pr.get("title") or "",
        pr_body=pr.get("body") or "",
        head_sha=head_sha,
        _job_id=job_id,
    )


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
    db: Annotated[AsyncSession, Depends(get_db)],
    x_github_event: str = Header(default=""),
    x_github_delivery: str = Header(default=""),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()

    if not settings.github_webhook_secret:
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    try:
        verify_signature(
            secret=settings.github_webhook_secret,
            body=body,
            signature_header=x_hub_signature_256,
        )
    except WebhookVerificationError as exc:
        logger.warning(
            "webhook_signature_rejected",
            delivery_id=x_github_delivery,
            github_event=x_github_event,
            reason=str(exc),
        )
        raise HTTPException(status_code=401, detail="Invalid signature") from exc

    payload = await request.json()

    action = payload.get("action")
    repository = payload.get("repository", {}).get("full_name")
    pr = payload.get("pull_request") or {}
    pr_number = pr.get("number")

    logger.info(
        "webhook_received",
        delivery_id=x_github_delivery,
        github_event=x_github_event,
        action=action,
        repository=repository,
        pr_number=pr_number,
    )

    is_reviewable_pr = (
        x_github_event == "pull_request"
        and action in REVIEWABLE_ACTIONS
        and repository
        and pr_number
        and not pr.get("draft")
        and (payload.get("installation") or {}).get("id")
    )

    # Persist every verified delivery. The delivery_id unique constraint
    # deduplicates GitHub retries at the database level.
    event_row = WebhookEvent(
        delivery_id=x_github_delivery,
        event=x_github_event,
        action=action,
        repository=repository,
        pr_number=pr_number,
        payload=payload,
    )
    db.add(event_row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

        # A duplicate delivery is only a true no-op when the review has
        # already published. Otherwise it is an operator-initiated retry
        # (GitHub's "Redeliver" button after a failure) and must re-enqueue.
        if is_reviewable_pr:
            owner, repo_name = repository.split("/", 1)
            head_sha = (pr.get("head") or {}).get("sha") or ""

            published_run_id = await db.scalar(
                select(ReviewRun.id).where(
                    ReviewRun.repo_owner == owner,
                    ReviewRun.repo_name == repo_name,
                    ReviewRun.pr_number == pr_number,
                    ReviewRun.head_sha == head_sha,
                    ReviewRun.config_version == settings.config_version,
                    ReviewRun.status == RunStatus.PUBLISHED,
                )
            )

            if published_run_id is None:
                await _enqueue_review(
                    arq_pool,
                    payload=payload,
                    repository=repository,
                    pr=pr,
                    pr_number=pr_number,
                    delivery_id=x_github_delivery,
                    is_retry=True,
                )
                logger.info(
                    "review_retry_enqueued",
                    delivery_id=x_github_delivery,
                    repository=repository,
                    pr_number=pr_number,
                )
                return {"status": "retry_enqueued", "delivery_id": x_github_delivery}

        logger.info("webhook_duplicate_delivery", delivery_id=x_github_delivery)
        return {"status": "duplicate", "delivery_id": x_github_delivery}

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "not_a_pull_request_event"}

    if action not in REVIEWABLE_ACTIONS:
        return {"status": "ignored", "reason": f"action_{action}_not_reviewable"}

    if pr.get("draft"):
        return {"status": "ignored", "reason": "draft_pr"}

    if not is_reviewable_pr:
        raise HTTPException(status_code=400, detail="Missing installation ID")

    await _enqueue_review(
        arq_pool,
        payload=payload,
        repository=repository,
        pr=pr,
        pr_number=pr_number,
        delivery_id=x_github_delivery,
    )

    logger.info(
        "review_job_enqueued",
        delivery_id=x_github_delivery,
        repository=repository,
        pr_number=pr_number,
    )
    return {"status": "accepted", "delivery_id": x_github_delivery}
