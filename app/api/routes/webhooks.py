import structlog
from fastapi import APIRouter, Header, HTTPException, Request

from app.core.config import settings
from app.github.webhook_verifier import WebhookVerificationError, verify_signature

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = structlog.get_logger(__name__)


@router.post("/github", status_code=202)
async def github_webhook(
    request: Request,
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
    pr_number = (payload.get("pull_request") or {}).get("number")

    logger.info(
        "webhook_received",
        delivery_id=x_github_delivery,
        github_event=x_github_event,
        action=action,
        repository=repository,
        pr_number=pr_number,
    )

    # Phase 2B: enqueue review job here
    return {"status": "accepted", "delivery_id": x_github_delivery}
