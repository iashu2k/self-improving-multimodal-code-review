import hashlib
import hmac
import json
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.api.routes.webhooks as webhook_routes
from app.core.config import settings
from app.db.models.feedback import CommentFeedback, FeedbackLabel
from app.db.models.review import CommentStatus, ReviewRun, RunStatus, StoredReviewComment
from app.db.session import get_db
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
  github_event: str = "pull_request",
  secret: str = TEST_SECRET,
  delivery_id: str = "test-delivery-1",
  include_signature: bool = True,
) -> dict[str, str]:
  headers = {
    "X-GitHub-Event": github_event,
    "X-GitHub-Delivery": delivery_id,
    "Content-Type": "application/json",
  }
  if include_signature:
    headers["X-Hub-Signature-256"] = sign(body, secret)
  return headers


class FakeArqPool:
  def __init__(self) -> None:
    self.jobs: list[dict] = []

  async def enqueue_job(self, function: str, **kwargs) -> None:
    self.jobs.append({"function": function, **kwargs})


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch: pytest.MonkeyPatch) -> None:
  monkeypatch.setattr(settings, "github_webhook_secret", TEST_SECRET)


@pytest.fixture(autouse=True)
def fake_arq_pool() -> FakeArqPool:
  pool = FakeArqPool()
  app.state.arq_pool = pool
  yield pool
  del app.state.arq_pool


@pytest.fixture(autouse=True)
def override_db(db_session):
  async def _override():
    yield db_session

  app.dependency_overrides[get_db] = _override
  yield
  app.dependency_overrides.clear()


@pytest.fixture
def client() -> AsyncClient:
  transport = ASGITransport(app=app)
  return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_signature_is_accepted(client: AsyncClient) -> None:
  """Success-path regression test (would have caught the structlog
  'event' kwarg collision that produced a 500 after verification)."""
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
  """Verification applies to all events; filtering happens after verification."""
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
  assert response.json()["status"] == "ignored"


# ---------------------------------------------------------------------------
# Job enqueue behavior
# ---------------------------------------------------------------------------


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
      "head": {"sha": "abc123def456"},
    },
    "installation": {"id": 12345},
  }
  body = json.dumps(payload).encode()

  async with client:
    response = await client.post(
      "/api/v1/webhooks/github",
      content=body,
      headers=make_headers(body, delivery_id="enqueue-delivery-1"),
    )

  assert response.status_code == 202
  assert len(fake_arq_pool.jobs) == 1
  job = fake_arq_pool.jobs[0]
  assert job["function"] == "run_pr_review"
  assert job["installation_id"] == 12345
  assert job["repository_owner"] == "owner"
  assert job["head_sha"] == "abc123def456"
  assert job["_job_id"] == "review-owner/review-sandbox-1-abc123de"


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
      headers=make_headers(body, delivery_id="draft-delivery-1"),
    )

  assert response.status_code == 202
  assert response.json()["status"] == "ignored"
  assert response.json()["reason"] == "draft_pr"
  assert fake_arq_pool.jobs == []


# ---------------------------------------------------------------------------
# Delivery deduplication (Phase 3A)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redelivery_without_published_run_retries(
  client: AsyncClient, fake_arq_pool: FakeArqPool
) -> None:
  """Redelivery with no published run = operator retry → re-enqueue."""
  body = json.dumps(make_pr_payload()).encode()
  headers = make_headers(body, delivery_id="dup-delivery-1")

  async with client:
    first = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    second = await client.post("/api/v1/webhooks/github", content=body, headers=headers)

  assert first.json()["status"] == "accepted"
  assert second.json()["status"] == "retry_enqueued"
  assert len(fake_arq_pool.jobs) == 2


@pytest.mark.asyncio
async def test_redelivery_after_publish_is_duplicate(
  client: AsyncClient, fake_arq_pool: FakeArqPool, db_session
) -> None:
  """Redelivery after a published review = true duplicate → no re-enqueue."""
  from app.db.models.review import ReviewRun, RunStatus

  db_session.add(
    ReviewRun(
      repo_owner="owner",
      repo_name="review-sandbox",
      pr_number=1,
      head_sha="deadbeef",
      config_version=settings.config_version,
      status=RunStatus.PUBLISHED,
    )
  )
  await db_session.commit()

  body = json.dumps(make_pr_payload()).encode()
  headers = make_headers(body, delivery_id="dup-delivery-2")

  async with client:
    first = await client.post("/api/v1/webhooks/github", content=body, headers=headers)
    second = await client.post("/api/v1/webhooks/github", content=body, headers=headers)

  assert first.json()["status"] == "accepted"
  assert second.json()["status"] == "duplicate"
  assert len(fake_arq_pool.jobs) == 1


class FakeFeedbackGitHub:
  def __init__(self) -> None:
    self.parent_comment: dict | None = None
    self.requests: list[tuple[str, str, int]] = []

  async def get_pr_review_comment(
    self,
    owner: str,
    repo: str,
    comment_id: int,
  ) -> dict:
    self.requests.append((owner, repo, comment_id))
    assert self.parent_comment is not None
    return self.parent_comment

  async def aclose(self) -> None:
    return None


@pytest.fixture
def fake_feedback_github(monkeypatch: pytest.MonkeyPatch) -> FakeFeedbackGitHub:
  client = FakeFeedbackGitHub()

  async def fake_get_installation_token(installation_id: int) -> str:
    assert installation_id == 42
    return "feedback-test-token"

  monkeypatch.setattr(
    webhook_routes,
    "get_installation_token",
    fake_get_installation_token,
  )
  monkeypatch.setattr(
    webhook_routes,
    "GitHubClient",
    lambda token: client,
  )

  return client


def make_feedback_payload(body: str = "/review-ai false-positive Already guarded.") -> dict:
  return {
    "action": "created",
    "repository": {"full_name": "owner/repo"},
    "installation": {"id": 42},
    "pull_request": {"number": 99},
    "comment": {
      "id": 700,
      "in_reply_to_id": 600,
      "body": body,
      "created_at": "2026-08-17T23:20:00Z",
      "author_association": "MEMBER",
    },
    "sender": {
      "login": "maintainer-user",
      "type": "User",
      "site_admin": False,
    },
  }


async def seed_feedback_target(db_session) -> ReviewRun:
  run = ReviewRun(
    repo_owner="owner",
    repo_name="repo",
    pr_number=99,
    head_sha="a" * 40,
    config_version=settings.config_version,
    status=RunStatus.PUBLISHED,
    github_review_id=333,
  )
  db_session.add(run)
  await db_session.flush()

  db_session.add(
    StoredReviewComment(
      run_id=run.id,
      file_path="src/client.py",
      line=24,
      severity="high",
      category="bug_risk",
      title="Missing null guard",
      body="The client can receive a missing token.",
      suggested_fix="Return before invoking the client when token is missing.",
      confidence=0.91,
      status=CommentStatus.POSTED,
    )
  )
  await db_session.commit()

  return run


@pytest.mark.asyncio
async def test_feedback_command_reply_is_persisted(
  client: AsyncClient,
  db_session,
  fake_feedback_github: FakeFeedbackGitHub,
) -> None:
  run = await seed_feedback_target(db_session)
  fake_feedback_github.parent_comment = {
    "id": 600,
    "pull_request_review_id": run.github_review_id,
    "path": "src/client.py",
    "line": 24,
    "body": (
      "Bot review finding.\n\n"
      f'<!-- review-forge {{"file":"src/client.py","line":24,"run_id":{run.id}}} -->'
    ),
  }

  payload = make_feedback_payload()
  body = json.dumps(payload).encode()

  async with client:
    response = await client.post(
      "/api/v1/webhooks/github",
      content=body,
      headers=make_headers(
        body,
        github_event="pull_request_review_comment",
        delivery_id="feedback-delivery-001",
      ),
    )

  feedback_rows = (
    await db_session.scalars(select(CommentFeedback).order_by(CommentFeedback.created_at))
  ).all()

  assert response.status_code == 202
  assert response.json()["status"] == "feedback_recorded"
  assert fake_feedback_github.requests == [("owner", "repo", 600)]
  assert len(feedback_rows) == 1
  assert feedback_rows[0].run_id == run.id
  assert feedback_rows[0].label == FeedbackLabel.FALSE_POSITIVE
  assert feedback_rows[0].created_at.replace(tzinfo=UTC) == datetime(
    2026,
    8,
    17,
    23,
    20,
    tzinfo=UTC,
  )


@pytest.mark.asyncio
async def test_non_command_review_comment_is_ignored(
  client: AsyncClient,
  fake_feedback_github: FakeFeedbackGitHub,
) -> None:
  payload = make_feedback_payload("This looks like a false positive.")
  body = json.dumps(payload).encode()

  async with client:
    response = await client.post(
      "/api/v1/webhooks/github",
      content=body,
      headers=make_headers(
        body,
        github_event="pull_request_review_comment",
        delivery_id="feedback-delivery-002",
      ),
    )

  assert response.status_code == 202
  assert response.json()["status"] == "ignored"
  assert response.json()["reason"] == "not_feedback_command"
  assert fake_feedback_github.requests == []


@pytest.mark.asyncio
async def test_unattributable_feedback_command_is_ignored(
  client: AsyncClient,
  db_session,
  fake_feedback_github: FakeFeedbackGitHub,
) -> None:
  run = await seed_feedback_target(db_session)
  fake_feedback_github.parent_comment = {
    "id": 600,
    "pull_request_review_id": run.github_review_id,
    "path": "src/other.py",
    "line": 24,
    "body": (
      "Copied marker.\n\n"
      f'<!-- review-forge {{"file":"src/client.py","line":24,"run_id":{run.id}}} -->'
    ),
  }

  payload = make_feedback_payload()
  body = json.dumps(payload).encode()

  async with client:
    response = await client.post(
      "/api/v1/webhooks/github",
      content=body,
      headers=make_headers(
        body,
        github_event="pull_request_review_comment",
        delivery_id="feedback-delivery-003",
      ),
    )

  feedback_rows = (await db_session.scalars(select(CommentFeedback))).all()

  assert response.status_code == 202
  assert response.json()["status"] == "ignored"
  assert response.json()["reason"] == "feedback_not_attributable"
  assert len(feedback_rows) == 0
