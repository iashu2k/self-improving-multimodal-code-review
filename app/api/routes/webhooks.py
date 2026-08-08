from typing import Annotated

import structlog
from arq import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.dependencies import get_arq_pool
from app.core.config import settings
from app.github.webhook_verifier import WebhookVerificationError, verify_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)

REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
    arq_pool: Annotated[ArqRedis, Depends(get_arq_pool)],
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

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": "not_a_pull_request_event"}

    if action not in REVIEWABLE_ACTIONS:
        return {"status": "ignored", "reason": f"action_{action}_not_reviewable"}

    if pr.get("draft"):
        return {"status": "ignored", "reason": "draft_pr"}

    installation_id = (payload.get("installation") or {}).get("id")
    if not installation_id:
        raise HTTPException(status_code=400, detail="Missing installation ID")

    owner, repo_name = repository.split("/", 1)

    await arq_pool.enqueue_job(
        "run_pr_review",
        installation_id=installation_id,
        repository_owner=owner,
        repository_name=repo_name,
        pr_number=pr_number,
        pr_title=pr.get("title") or "",
        pr_body=pr.get("body") or "",
        head_sha=(pr.get("head") or {}).get("sha") or "",
        _job_id=f"review-{repository}-{pr_number}-{x_github_delivery}",
    )

    logger.info(
        "review_job_enqueued",
        delivery_id=x_github_delivery,
        repository=repository,
        pr_number=pr_number,
    )
    return {"status": "accepted", "delivery_id": x_github_delivery}
