from sqlalchemy import select

from app.db.models.eval import EvalExampleResult
from app.evals import store
from app.evals.schemas import ExampleMetrics, SystemName


async def test_record_example_result_persists_generated_comments(db_session) -> None:
  run = await store.create_run(
    db_session,
    config_version="test-generated-comments",
    dataset_split="development",
    systems=[SystemName.BASELINE_A],
  )
  comments = [
    {
      "file_path": "src/example.py",
      "line": 12,
      "category": "bug_risk",
      "severity": "medium",
      "title": "Potential None dereference",
      "body": "The added call can receive None.",
      "suggested_fix": "Guard the value before calling it.",
      "evidence": "result.process()",
    }
  ]
  metrics = ExampleMetrics(
    example_id="example-1",
    system=SystemName.BASELINE_A,
    tp=0,
    fp=1,
    fn=1,
    total_comments=1,
  )

  row = await store.record_example_result(
    db_session,
    run_id=run.id,
    metrics=metrics,
    generated_comments=comments,
    cost_usd=0.0,
  )
  await db_session.commit()

  stored = await db_session.scalar(select(EvalExampleResult).where(EvalExampleResult.id == row.id))

  assert stored is not None
  assert stored.generated_comments == comments


async def test_record_example_result_persists_empty_comments(db_session) -> None:
  run = await store.create_run(
    db_session,
    config_version="test-empty-generated-comments",
    dataset_split="development",
    systems=[SystemName.BASELINE_A],
  )
  row = await store.record_example_result(
    db_session,
    run_id=run.id,
    metrics=ExampleMetrics(
      example_id="empty-example",
      system=SystemName.BASELINE_A,
      expected_empty=True,
      predicted_empty=True,
      total_comments=0,
    ),
    generated_comments=[],
    cost_usd=0.0,
  )
  await db_session.commit()

  stored = await db_session.scalar(select(EvalExampleResult).where(EvalExampleResult.id == row.id))

  assert stored is not None
  assert stored.generated_comments == []
