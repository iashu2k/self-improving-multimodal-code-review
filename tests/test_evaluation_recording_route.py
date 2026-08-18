import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.db.session import get_db
from app.main import app


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


async def seed_candidate(db_session) -> ReviewConfiguration:
  config = ReviewConfiguration(
    config_version="v1.2",
    parent_version="v1.1",
    change_reason="Candidate for evaluation recording.",
    status=ConfigurationStatus.DRAFT,
    generator_prompt_version="generator_v1.2",
    critic_prompt_version="critic_v1.1",
  )
  db_session.add(config)
  await db_session.commit()
  return config


@pytest.mark.asyncio
async def test_record_evaluation_metrics(client: AsyncClient, db_session) -> None:
  candidate = await seed_candidate(db_session)

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{candidate.id}/evaluations",
      json={
        "dataset_split": "validation",
        "system": "final_agent",
        "repeat_number": 1,
        "precision": 0.14,
        "recall": 0.23,
        "f1": 0.17,
        "groundedness": 0.92,
        "abstention_accuracy": 0.75,
        "no_comment_accuracy": 1.0,
        "safety_policy_failures": 0,
        "metrics": {"run_label": "v8-val-v12-r1"},
      },
    )

  assert response.status_code == 201
  payload = response.json()
  assert payload["configuration_id"] == str(candidate.id)
  assert payload["dataset_split"] == "validation"
  assert payload["system"] == "final_agent"
  assert payload["repeat_number"] == 1
  assert payload["precision"] == 0.14
  assert payload["recall"] == 0.23
  assert payload["groundedness"] == 0.92
  assert payload["safety_policy_failures"] == 0


@pytest.mark.asyncio
async def test_record_evaluation_rejects_holdout_split(
  client: AsyncClient,
  db_session,
) -> None:
  candidate = await seed_candidate(db_session)

  async with client:
    response = await client.post(
      f"/api/v1/configurations/{candidate.id}/evaluations",
      json={
        "dataset_split": "holdout",
        "system": "final_agent",
        "repeat_number": 1,
        "precision": 0.14,
        "recall": 0.23,
        "f1": 0.17,
        "groundedness": 0.92,
        "abstention_accuracy": 0.75,
        "no_comment_accuracy": 1.0,
        "safety_policy_failures": 0,
      },
    )

  assert response.status_code == 400
  assert "holdout" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_record_evaluation_returns_404_for_unknown_configuration(
  client: AsyncClient,
) -> None:
  async with client:
    response = await client.post(
      f"/api/v1/configurations/{uuid.uuid4()}/evaluations",
      json={
        "dataset_split": "validation",
        "system": "final_agent",
        "repeat_number": 1,
        "precision": 0.14,
        "recall": 0.23,
        "f1": 0.17,
        "groundedness": 0.92,
        "abstention_accuracy": 0.75,
        "no_comment_accuracy": 1.0,
        "safety_policy_failures": 0,
      },
    )

  assert response.status_code == 404
