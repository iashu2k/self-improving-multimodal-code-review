import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db
from app.main import app
from app.services.configurations import create_configuration_candidate


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


def candidate_payload(version: str = "v1.2") -> dict:
  return {
    "config_version": version,
    "parent_version": "v1.1",
    "change_reason": "Reduce false positives for missing-null-check comments.",
    "generator_prompt_version": f"generator_{version}",
    "critic_prompt_version": "critic_v1.1",
    "router_rules": {},
    "thresholds": {
      "minimum_confidence": 0.78,
      "max_comments_per_pr": 5,
    },
    "model_versions": {"review": "anthropic/claude-sonnet-4.5"},
    "retrieval_config": {"top_k": 8},
    "repair_policy": {"max_repairs": 2},
    "created_by": "manual",
  }


@pytest.mark.asyncio
async def test_create_configuration_candidate(client: AsyncClient) -> None:
  async with client:
    response = await client.post(
      "/api/v1/configurations",
      json=candidate_payload(),
    )

  assert response.status_code == 201
  payload = response.json()
  assert payload["config_version"] == "v1.2"
  assert payload["parent_version"] == "v1.1"
  assert payload["status"] == "draft"
  assert payload["thresholds"]["minimum_confidence"] == 0.78
  assert payload["approval_status"] == "pending_approval"


@pytest.mark.asyncio
async def test_create_configuration_candidate_conflict(client: AsyncClient, db_session) -> None:
  await create_configuration_candidate(
    db_session,
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Existing candidate.",
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )
  await db_session.commit()

  async with client:
    response = await client.post(
      "/api/v1/configurations",
      json=candidate_payload(),
    )

  assert response.status_code == 409


@pytest.mark.asyncio
async def test_approve_configuration(client: AsyncClient, db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.3",
    parent_version="v1.2",
    change_reason="Approval route test.",
    generator_prompt_version="generator_v1.3",
    critic_prompt_version="critic_v1.2",
  )
  await db_session.commit()

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{config.id}/approve",
      json={"approved_by": "senior-maintainer"},
    )

  assert response.status_code == 200
  payload = response.json()
  assert payload["status"] == "pending"
  assert payload["approved_by"] == "senior-maintainer"
  assert payload["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_reject_configuration(client: AsyncClient, db_session) -> None:
  config = await create_configuration_candidate(
    db_session,
    config_version="v1.4",
    parent_version="v1.3",
    change_reason="Rejection route test.",
    generator_prompt_version="generator_v1.4",
    critic_prompt_version="critic_v1.3",
  )
  await db_session.commit()

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{config.id}/reject",
      json={"reason": "Candidate regressed groundedness."},
    )

  assert response.status_code == 200
  payload = response.json()
  assert payload["status"] == "rejected"
  assert payload["rejection_reason"] == "Candidate regressed groundedness."


@pytest.mark.asyncio
async def test_unknown_configuration_returns_404(client: AsyncClient) -> None:
  async with client:
    response = await client.post(
      f"/api/v1/configurations/{uuid.uuid4()}/approve",
      json={"approved_by": "senior-maintainer"},
    )

  assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_configurations(client: AsyncClient, db_session) -> None:
  await create_configuration_candidate(
    db_session,
    config_version="v1.5",
    parent_version="v1.4",
    change_reason="List route test.",
    generator_prompt_version="generator_v1.5",
    critic_prompt_version="critic_v1.4",
  )
  await db_session.commit()

  async with client:
    response = await client.get("/api/v1/configurations")

  assert response.status_code == 200
  payload = response.json()
  assert payload["total"] == 1
  assert payload["items"][0]["config_version"] == "v1.5"
