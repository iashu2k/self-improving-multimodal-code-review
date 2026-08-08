import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

TEST_SECRET = "test-webhook-secret-0123456789abcdef0123456789abcdef"


def sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_pr_payload(action: str = "opened") -> dict:
    return {
        "action": action,
        "repository": {"full_name": "owner/review-sandbox"},
        "pull_request": {
            "number": 1,
            "title": "Test PR",
            "body": "",
            "draft": False,
            "head": {"sha": "deadbeef"},
        },
        "installation": {"id": 42},
    }


def make_headers(
    body: bytes,
    *,
    secret: str = TEST_SECRET,
    delivery_id: str = "test-delivery-1",
    include_signature: bool = True,
) -> dict[str, str]:
    headers = {
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id,
        "Content-Type": "application/json",
    }
    if include_signature:
        headers["X-Hub-Signature-256"] = sign(body, secret)
    return headers


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "github_webhook_secret", TEST_SECRET)


@pytest.fixture
def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_valid_signature_is_accepted(client: AsyncClient) -> None:
    """A correctly signed pull_request event returns 202 and echoes the delivery ID.

    This is the success-path regression test: it would have caught the structlog
    'event' kwarg collision that produced a 500 after signature verification.
    """
    body = json.dumps(make_pr_payload()).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body),
        )

    assert response.status_code == 202
    assert response.json()["delivery_id"] == "test-delivery-1"


@pytest.mark.asyncio
async def test_valid_signature_with_synchronize_action(client: AsyncClient) -> None:
    body = json.dumps(make_pr_payload(action="synchronize")).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body, delivery_id="test-delivery-sync"),
        )

    assert response.status_code == 202
    assert response.json()["delivery_id"] == "test-delivery-sync"


@pytest.mark.asyncio
async def test_invalid_signature_is_rejected(client: AsyncClient) -> None:
    body = json.dumps(make_pr_payload()).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body, secret="wrong-secret", delivery_id="test-delivery-2"),
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_missing_signature_is_rejected(client: AsyncClient) -> None:
    body = json.dumps(make_pr_payload()).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body, include_signature=False, delivery_id="test-delivery-3"),
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_malformed_signature_prefix_is_rejected(client: AsyncClient) -> None:
    body = json.dumps(make_pr_payload()).encode()
    headers = make_headers(body, delivery_id="test-delivery-4")
    headers["X-Hub-Signature-256"] = "sha1=deadbeef"

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=headers,
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_tampered_body_is_rejected(client: AsyncClient) -> None:
    """Sign one body, send another — signature must not validate."""
    original_body = json.dumps(make_pr_payload()).encode()
    tampered_body = json.dumps(make_pr_payload(action="closed")).encode()

    headers = make_headers(original_body, delivery_id="test-delivery-5")

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=tampered_body,
            headers=headers,
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_non_pr_event_with_valid_signature_is_accepted(client: AsyncClient) -> None:
    """Verification applies to all events; routing/filtering happens after verification."""
    body = json.dumps({"action": "created", "repository": {"full_name": "owner/repo"}}).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers={
                **make_headers(body, delivery_id="test-delivery-6"),
                "X-GitHub-Event": "ping",
            },
        )

    assert response.status_code == 202


class FakeArqPool:
    def __init__(self) -> None:
        self.jobs: list[dict] = []

    async def enqueue_job(self, function: str, **kwargs) -> None:
        self.jobs.append({"function": function, **kwargs})


@pytest.fixture(autouse=True)
def fake_arq_pool() -> FakeArqPool:
    pool = FakeArqPool()
    app.state.arq_pool = pool
    yield pool
    del app.state.arq_pool


@pytest.mark.asyncio
async def test_pr_opened_enqueues_review_job(
    client: AsyncClient, fake_arq_pool: FakeArqPool
) -> None:
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/review-sandbox"},
        "pull_request": {
            "number": 1,
            "title": "Risky change",
            "body": "details",
            "draft": False,
            "head": {"sha": "abc123"},
        },
        "installation": {"id": 12345},
    }
    body = json.dumps(payload).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body),
        )

    assert response.status_code == 202
    assert len(fake_arq_pool.jobs) == 1
    job = fake_arq_pool.jobs[0]
    assert job["function"] == "run_pr_review"
    assert job["installation_id"] == 12345
    assert job["repository_owner"] == "owner"
    assert job["head_sha"] == "abc123"


@pytest.mark.asyncio
async def test_draft_pr_does_not_enqueue(client: AsyncClient, fake_arq_pool: FakeArqPool) -> None:
    payload = {
        "action": "opened",
        "repository": {"full_name": "owner/review-sandbox"},
        "pull_request": {"number": 1, "draft": True, "head": {"sha": "abc"}},
        "installation": {"id": 1},
    }
    body = json.dumps(payload).encode()

    async with client:
        response = await client.post(
            "/api/v1/webhooks/github",
            content=body,
            headers=make_headers(body),
        )

    assert response.status_code == 202
    assert response.json()["status"] == "ignored"
    assert fake_arq_pool.jobs == []
