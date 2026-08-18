"""Phase 8 demo: promotion-gate walkthrough (v1.1 active -> v1.2 promoted).

Flow:
  0. Reject stale demo candidates from previous runs.
  1. v1.1 is active; a v1.2 draft candidate is seeded.
  2. Promotion with NO metrics -> hard rejection (no evaluation aggregate).
  3. Validation metrics recorded for baseline and candidate.
  4. Promotion without human approval -> gate returns eligible=false.
  5. Human approval recorded.
  6. Promotion succeeds: v1.2 ACTIVE, v1.1 ROLLED_BACK (rollback target).
  7. Rollback restores v1.1, making the demo re-runnable.

Run:  uv run python scripts/demo_phase8_promotion.py
"""

import asyncio
import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models.config import ConfigurationStatus, ReviewConfiguration
from app.db.session import get_db
from app.main import app

BASELINE_METRICS = {
  "precision": 0.14,
  "recall": 0.23,
  "f1": 0.17,
  "groundedness": 0.92,
  "abstention_accuracy": 0.75,
  "no_comment_accuracy": 1.0,
  "safety_policy_failures": 0,
}

CANDIDATE_METRICS = {
  "precision": 0.18,
  "recall": 0.29,
  "f1": 0.22,
  "groundedness": 0.95,
  "abstention_accuracy": 0.82,
  "no_comment_accuracy": 1.0,
  "safety_policy_failures": 0,
}

APPROVAL_BODY = {"approved_by": "demo-human"}
PROMOTION_BODY = {"system": "final_agent"}


async def get_session():
  agen = get_db()
  session = await agen.__anext__()
  return agen, session


async def print_state(session, label: str) -> None:
  result = await session.execute(
    select(ReviewConfiguration).order_by(ReviewConfiguration.created_at)
  )
  print(f"  --- {label} ---")
  for config in result.scalars():
    status = getattr(config.status, "value", config.status)
    print(
      f"  {config.config_version:<22} status={status:<12} approved_by={config.approved_by or '-'}"
    )


async def main() -> None:
  transport = ASGITransport(app=app)
  agen, session = await get_session()

  try:
    async with AsyncClient(transport=transport, base_url="http://demo") as client:
      print("\n=== STEP 0: reject stale demo candidates ===")
      stale = await session.execute(
        select(ReviewConfiguration).where(
          ReviewConfiguration.config_version.like("v1.2-demo-%"),
          ReviewConfiguration.status.in_([ConfigurationStatus.DRAFT, ConfigurationStatus.PENDING]),
        )
      )
      for config in stale.scalars():
        response = await client.post(
          f"/api/v1/configurations/{config.id}/reject",
          json={"reason": "superseded by a newer demo run"},
        )
        print(f"  rejected {config.config_version}: {response.status_code}")

      print("\n=== STEP 1: seed baseline (v1.1) and candidate (v1.2) ===")
      active = (
        (
          await session.execute(
            select(ReviewConfiguration).where(
              ReviewConfiguration.status == ConfigurationStatus.ACTIVE
            )
          )
        )
        .scalars()
        .first()
      )
      if active is not None and active.config_version.startswith("v1.2-demo-"):
        response = await client.post(
          "/api/v1/configurations/rollback",
          json={"reason": "demo restart; restoring baseline"},
        )
        print(f"  rollback from stale demo state: {response.status_code}")
        await session.reset()
        active = (
          (
            await session.execute(
              select(ReviewConfiguration).where(
                ReviewConfiguration.status == ConfigurationStatus.ACTIVE
              )
            )
          )
          .scalars()
          .first()
        )
      if active is None:
        active = ReviewConfiguration(
          config_version="v1.1",
          change_reason="Baseline configuration for Phase 8 demo.",
          status=ConfigurationStatus.ACTIVE,
          generator_prompt_version="generator_v1.1",
          critic_prompt_version="critic_v1.1",
        )
        session.add(active)
        await session.commit()
        print(f"  seeded baseline: {active.config_version}")
      else:
        print(f"  active configuration: {active.config_version}")

      candidate = ReviewConfiguration(
        config_version=f"v1.2-demo-{uuid.uuid4().hex[:6]}",
        parent_version=active.config_version,
        change_reason="Candidate: tightened groundedness language in generator prompt.",
        status=ConfigurationStatus.DRAFT,
        generator_prompt_version="generator_v1.2",
        critic_prompt_version="critic_v1.1",
      )
      session.add(candidate)
      await session.commit()
      print(f"  seeded candidate: {candidate.config_version}")
      baseline_id, candidate_id = active.id, candidate.id

      print("\n=== STEP 2: promote with NO metrics (must be rejected) ===")
      response = await client.post(
        f"/api/v1/configurations/{candidate_id}/promote", json=PROMOTION_BODY
      )
      print(f"  status={response.status_code}  body={response.json()}")
      assert response.status_code >= 400, "promotion without metrics must fail"

      print("\n=== STEP 3: record validation metrics ===")
      for config_id, metrics, label in (
        (baseline_id, BASELINE_METRICS, "baseline-v11"),
        (candidate_id, CANDIDATE_METRICS, "candidate-v12"),
      ):
        for repeat in (1, 2, 3):
          response = await client.post(
            f"/api/v1/configurations/{config_id}/evaluations",
            json={
              "dataset_split": "validation",
              "system": "final_agent",
              "repeat_number": repeat,
              **metrics,
              "metrics": {"run_label": f"{label}-r{repeat}"},
            },
          )
          assert response.status_code == 201, response.text
        print(f"  recorded 3 validation repeats for {label}")

      print("\n=== STEP 4: promote without approval (gate must say no) ===")
      response = await client.post(
        f"/api/v1/configurations/{candidate_id}/promote", json=PROMOTION_BODY
      )
      decision = response.json()
      print(f"  status={response.status_code}  decision={decision}")
      assert response.status_code == 200
      assert decision["eligible"] is False
      assert decision["failed_conditions"], "gate must report failed conditions"

      print("\n=== STEP 5: human approval ===")
      response = await client.post(
        f"/api/v1/configurations/{candidate_id}/approve", json=APPROVAL_BODY
      )
      assert response.status_code == 200, response.text
      print(f"  approved: {response.json()['approved_by']}")

      print("\n=== STEP 6: promote (gate must pass) ===")
      response = await client.post(
        f"/api/v1/configurations/{candidate_id}/promote", json=PROMOTION_BODY
      )
      decision = response.json()
      print(f"  status={response.status_code}  decision={decision}")
      assert decision["eligible"] is True, decision

      print("\n=== STEP 7: verify lifecycle state ===")
      await session.reset()
      await print_state(session, "after promotion")

      print("\n=== STEP 8: rollback to v1.1 (restores demo start state) ===")
      response = await client.post(
        "/api/v1/configurations/rollback",
        json={"reason": "demo complete; restoring baseline"},
      )
      print(f"  status={response.status_code}  body={response.json()}")
      await session.reset()
      await print_state(session, "after rollback")

      print("\nDemo complete: gate rejected unproven promotion, required human")
      print("approval, promoted on recorded evidence, and rolled back cleanly.\n")
  finally:
    await agen.aclose()


if __name__ == "__main__":
  asyncio.run(main())
