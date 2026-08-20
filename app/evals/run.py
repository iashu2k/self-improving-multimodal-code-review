"""Phase 7 offline evaluation harness entrypoint.


Usage:
  python -m app.evals.run --dataset holdout --config v1
  python -m app.evals.run --dataset holdout --config v1 --systems baseline_a,final_agent
  python -m app.evals.run --dataset holdout --config v1 --export data/processed/eval_v1


Requires golden examples to have snapshot_id populated (Phase 7.2 curation).
"""

import argparse
import asyncio
import csv
import json
from collections.abc import Sequence
from pathlib import Path

from app.agents.graph import run_review_graph
from app.core.config import settings
from app.db.session import get_session_maker
from app.eval.golden_schemas import GoldComment
from app.evals import judge, matcher, metrics, store
from app.evals.baselines import SystemOutput
from app.evals.schemas import AggregateMetrics, ExampleMetrics, RoutingConfusion, SystemName
from app.github.diff_parser import parse_unified_diff
from app.ingestion.retriever import (
  MAX_CONTEXT_QUERY_FILES,
  MAX_CONTEXTS_FOR_PROMPT,
  build_context_query,
  hybrid_retrieve,
)
from app.llm.prompts.review import EVAL_RELAXED_SYSTEM_PROMPT
from app.llm.reviewer import EVAL_RELAXED_GENERATOR_POLICY, generate_comments
from app.observability import flush, node_span, root_trace, score_trace

GOLDEN_ROOT = Path("data/golden")


class OpenRouterJudgeClient:
  def __init__(self, model: str) -> None:
    self.model = model
    self._client = None

  async def judge(
    self,
    system: str,
    user: str,
    schema_name: str,
    json_schema: dict,
  ) -> str:
    if self._client is None:
      from app.llm.openrouter_client import OpenRouterClient

      self._client = OpenRouterClient()

    response = await self._client.chat_structured(
      model=self.model,
      schema_name=schema_name,
      json_schema=json_schema,
      messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
      ],
      temperature=0.0,
      max_tokens=2000,
    )
    return json.dumps(response.content)


def load_golden_split(split: str) -> list[dict]:
  manifest = json.loads((GOLDEN_ROOT / "manifest.json").read_text())
  examples = []

  for entry in manifest.get("cases", []):
    if entry.get("kind") != "text" or entry.get("split") != split:
      continue

    example = json.loads((GOLDEN_ROOT / entry["paths"]["annotation"]).read_text())
    example["gold_comments"] = [
      GoldComment.model_validate(comment) for comment in example.get("gold_comments", [])
    ]

    diff_path = GOLDEN_ROOT / example["diff_path"]
    example["_diff_text"] = diff_path.read_text()
    example["_changed_files"] = parse_unified_diff(example["_diff_text"])
    examples.append(example)

  return examples


async def retrieve_contexts(
  changed_files,
  session,
  snapshot_id,
  llm,
  embedding_model,
):
  contexts = []

  for changed_file in changed_files[:MAX_CONTEXT_QUERY_FILES]:
    contexts.extend(
      await hybrid_retrieve(
        session,
        snapshot_id=snapshot_id,
        query_text=build_context_query(changed_file),
        llm=llm,
        embedding_model=embedding_model,
      )
    )

  return contexts[:MAX_CONTEXTS_FOR_PROMPT]


async def baseline_a_review(
  example,
  *,
  max_repairs: int = 0,
) -> SystemOutput:
  from app.llm.openrouter_client import OpenRouterClient

  client = OpenRouterClient()
  _, comments = await generate_comments(
    files=example["_changed_files"],
    pr_title=example["pr_metadata"]["title"],
    pr_body=example["pr_metadata"].get("description", ""),
    client=client,
    model=settings.openrouter_review_model,
  )

  return SystemOutput(
    system=SystemName.BASELINE_A,
    comments=comments,
    attempts=1,
  )


async def baseline_a_relaxed_review(
  example,
  *,
  max_repairs: int = 0,
) -> SystemOutput:
  from app.llm.openrouter_client import OpenRouterClient

  client = OpenRouterClient()
  _, comments = await generate_comments(
    files=example["_changed_files"],
    pr_title=example["pr_metadata"]["title"],
    pr_body=example["pr_metadata"].get("description", ""),
    client=client,
    model=settings.openrouter_review_model,
    system_prompt=EVAL_RELAXED_SYSTEM_PROMPT,
    generator_policy=EVAL_RELAXED_GENERATOR_POLICY,
  )

  return SystemOutput(
    system=SystemName.BASELINE_A_RELAXED,
    comments=comments,
    attempts=1,
  )


async def baseline_b_review(
  example,
  *,
  session,
  max_repairs: int = 0,
) -> SystemOutput:
  from app.llm.openrouter_client import OpenRouterClient

  client = OpenRouterClient()
  snapshot_id = example.get("snapshot_id")

  if snapshot_id is None:
    raise ValueError(f"Example {example['example_id']} missing snapshot_id — run curation first")

  contexts = await retrieve_contexts(
    example["_changed_files"],
    session,
    snapshot_id,
    client,
    settings.openrouter_embedding_model,
  )
  context_text = "\n\n".join(context.content for context in contexts)

  _, comments = await generate_comments(
    files=example["_changed_files"],
    pr_title=example["pr_metadata"]["title"],
    pr_body=example["pr_metadata"].get("description", ""),
    client=client,
    model=settings.openrouter_review_model,
    contexts=contexts,
  )

  return SystemOutput(
    system=SystemName.BASELINE_B,
    comments=comments,
    retrieved_context=context_text,
    attempts=1,
  )


async def final_agent_review(
  example,
  *,
  session,
  run_id,
  max_repairs: int = 2,
) -> SystemOutput:
  from app.llm.openrouter_client import OpenRouterClient

  client = OpenRouterClient()
  snapshot_id = example.get("snapshot_id")

  if snapshot_id is None:
    raise ValueError(f"Example {example['example_id']} missing snapshot_id — run curation first")

  output = await run_review_graph(
    session=session,
    llm=client,
    snapshot_id=snapshot_id,
    run_id=run_id,
    pr_number=0,
    commit_sha=example["commit_sha"],
    pr_title=example["pr_metadata"]["title"],
    pr_body=example["pr_metadata"].get("description", ""),
    diff=example["_diff_text"],
    changed_files=example["_changed_files"],
    config_version="eval",
    router_model=settings.openrouter_router_model or settings.openrouter_review_model,
    review_model=settings.openrouter_review_model,
    critic_model=settings.openrouter_critic_model or settings.openrouter_review_model,
    embedding_model=settings.openrouter_embedding_model,
  )

  events = [
    event.model_dump(mode="json") if hasattr(event, "model_dump") else event
    for event in output.events
  ]

  return SystemOutput(
    system=SystemName.FINAL_AGENT,
    comments=output.accepted,
    retrieved_context="",
    attempts=output.retry_count + 1,
    raw={"events": events},
  )


async def evaluate_system(
  system_name: SystemName,
  examples: Sequence[dict],
  judge_client: OpenRouterJudgeClient,
  session,
  run_id,
  max_repairs: int = 2,
) -> tuple[list[ExampleMetrics], dict[str, list[bool]], float]:
  per_example: list[ExampleMetrics] = []
  pass_attempts: dict[str, list[bool]] = {}
  total_cost = 0.0

  for example in examples:
    example_id = example["example_id"]

    # One span per example per system. Review and judge LLM generations
    # nest under it automatically via the active eval_run trace.
    async with node_span(
      "eval_example",
      input={"example_id": example_id, "system": system_name.value},
    ) as example_span:
      gold_comments = example.get("gold_comments", [])
      diff_text = example["_diff_text"]
      attempt_passes: list[bool] = []

      for attempt in range(1, max_repairs + 1):
        if system_name == SystemName.BASELINE_A:
          output = await baseline_a_review(example)
        elif system_name == SystemName.BASELINE_A_RELAXED:
          output = await baseline_a_relaxed_review(example)
        elif system_name == SystemName.BASELINE_B:
          output = await baseline_b_review(example, session=session)
        else:
          output = await final_agent_review(
            example,
            session=session,
            run_id=run_id,
            max_repairs=max_repairs,
          )

        generated = output.comments

        pairs = matcher.deterministic_candidates(gold_comments, generated)
        decisions = await judge.judge_equivalence(
          judge_client,
          example_id,
          pairs,
          gold_comments,
          generated,
          diff_text,
        )
        matches = matcher.resolve_matches(
          example_id,
          len(gold_comments),
          len(generated),
          pairs,
          decisions,
        )

        grounded = await judge.judge_groundedness(
          judge_client,
          generated,
          diff_text,
          output.retrieved_context,
        )
        grounded_flags = [result.grounded for result in grounded]
        line_valid_flags = [True] * len(generated)

        scored = metrics.score_example(
          example_id,
          system_name,
          gold_comments,
          generated,
          matches,
          grounded_flags,
          line_valid_flags,
          attempt=attempt,
        )

        row = await store.record_example_result(
          session,
          run_id=run_id,
          metrics=scored,
          generated_comments=[comment.model_dump(mode="json") for comment in generated],
          cost_usd=0.0,
        )
        await store.record_matches(
          session,
          run_id=run_id,
          example_result_id=row.id,
          matches=matches,
        )
        await session.commit()

        acceptable = scored.fn == 0 and scored.fp == 0
        attempt_passes.append(acceptable)

        if attempt == 1:
          per_example.append(scored)

      pass_attempts[example_id] = attempt_passes
      first_attempt = per_example[-1]
      example_span.update(
        output={
          "tp": first_attempt.tp,
          "fp": first_attempt.fp,
          "fn": first_attempt.fn,
          "precision": first_attempt.precision,
          "recall": first_attempt.recall,
          "f1": first_attempt.f1,
          "attempt_passes": attempt_passes,
        }
      )

  return per_example, pass_attempts, total_cost


def export_reports(
  out_dir: Path,
  aggregates: Sequence[AggregateMetrics],
  per_system_examples: dict[str, list[ExampleMetrics]],
  confusion: dict[str, RoutingConfusion],
) -> None:
  out_dir.mkdir(parents=True, exist_ok=True)

  (out_dir / "aggregates.json").write_text(
    json.dumps(
      [aggregate.model_dump(mode="json") for aggregate in aggregates],
      indent=2,
    )
  )

  with (out_dir / "per_example.csv").open("w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(
      [
        "system",
        "example_id",
        "tp",
        "fp",
        "fn",
        "precision",
        "recall",
        "f1",
        "expected_empty",
        "predicted_empty",
      ]
    )

    for system, rows in per_system_examples.items():
      for item in rows:
        writer.writerow(
          [
            system,
            item.example_id,
            item.tp,
            item.fp,
            item.fn,
            item.precision,
            item.recall,
            item.f1,
            item.expected_empty,
            item.predicted_empty,
          ]
        )

  with (out_dir / "routing_confusion.csv").open("w", newline="") as file:
    writer = csv.writer(file)
    writer.writerow(
      [
        "system",
        "true_comment",
        "false_comment",
        "true_abstain",
        "false_abstain",
      ]
    )

    for system, item in confusion.items():
      writer.writerow(
        [
          system,
          item.true_comment,
          item.false_comment,
          item.true_abstain,
          item.false_abstain,
        ]
      )

  failures = {
    system: [
      item.model_dump(mode="json")
      for item in rows
      if item.fp > 0 or item.fn > 0 or (item.expected_empty and not item.predicted_empty)
    ]
    for system, rows in per_system_examples.items()
  }
  (out_dir / "failure_examples.json").write_text(json.dumps(failures, indent=2))

  lines = [
    "# Baseline vs Final",
    "",
    "| System | P | R | F1 | Grounded | Line-valid | Severity | Abstain acc | pass@1 | pass@2 |",
    "|---|---|---|---|---|---|---|---|---|---|",
  ]

  for aggregate in aggregates:
    lines.append(
      f"| {aggregate.system.value} | {_fmt(aggregate.precision)} "
      f"| {_fmt(aggregate.recall)} | {_fmt(aggregate.f1)} "
      f"| {_fmt(aggregate.groundedness_rate)} "
      f"| {_fmt(aggregate.line_validity_rate)} "
      f"| {_fmt(aggregate.severity_agreement_rate)} "
      f"| {_fmt(aggregate.no_comment_accuracy)} "
      f"| {_fmt(aggregate.pass_at_1)} | {_fmt(aggregate.pass_at_2)} |"
    )

  (out_dir / "baseline_vs_final.md").write_text("\n".join(lines) + "\n")


def _fmt(value: float | None) -> str:
  return f"{value:.3f}" if value is not None else "—"


async def main() -> None:
  parser = argparse.ArgumentParser(description="Phase 7 offline evaluation harness")
  parser.add_argument(
    "--dataset",
    default="holdout",
    help="golden split to evaluate",
  )
  parser.add_argument(
    "--config",
    required=True,
    help="config version label, e.g. v1",
  )
  parser.add_argument(
    "--systems",
    default=None,
    help="comma-separated subset",
  )
  parser.add_argument(
    "--export",
    default=None,
    help="directory for CSV/JSON export",
  )
  parser.add_argument(
    "--judge-model",
    default=settings.openrouter_judge_model or "openai/gpt-4o-mini",
  )
  parser.add_argument(
    "--max-repairs",
    type=int,
    default=2,
  )
  args = parser.parse_args()

  examples = load_golden_split(args.dataset)

  if not examples:
    raise SystemExit(f"No golden examples found for split '{args.dataset}'")

  selected = (
    [SystemName(system) for system in args.systems.split(",")]
    if args.systems
    else [
      SystemName.BASELINE_A,
      SystemName.BASELINE_B,
      SystemName.FINAL_AGENT,
    ]
  )

  judge_client = OpenRouterJudgeClient(args.judge_model)
  session_maker = get_session_maker()

  try:
    async with session_maker() as session:
      run = await store.create_run(
        session,
        config_version=args.config,
        dataset_split=args.dataset,
        systems=selected,
      )
      aggregates: list[AggregateMetrics] = []
      per_system_examples: dict[str, list[ExampleMetrics]] = {}
      confusion: dict[str, RoutingConfusion] = {}

      # One trace per eval run. The seed is namespaced (eval-run-<id>)
      # so it can never collide with a review-run trace ID, and the
      # eval_run_id metadata links the trace back to the DB row.
      async with root_trace(
        "eval_run",
        trace_seed=f"eval-run-{run.id}",
        metadata={
          "eval_run_id": run.id,
          "config_version": args.config,
          "dataset_split": args.dataset,
          "systems": [system.value for system in selected],
          "judge_model": args.judge_model,
          "max_repairs": args.max_repairs,
        },
        version=args.config,
        tags=["eval", args.dataset, args.config],
      ) as trace:
        for system_name in selected:
          async with node_span(
            "eval_system",
            input={"system": system_name.value},
          ) as system_span:
            per_example, pass_attempts, cost = await evaluate_system(
              system_name,
              examples,
              judge_client,
              session,
              run.id,
              max_repairs=args.max_repairs,
            )
            aggregate = metrics.aggregate(
              system_name,
              args.dataset,
              per_example,
              pass_attempts,
              cost,
            )
            aggregates.append(aggregate)
            per_system_examples[system_name.value] = per_example
            confusion[system_name.value] = metrics.routing_confusion(per_example)
            system_span.update(
              output={
                "precision": aggregate.precision,
                "recall": aggregate.recall,
                "f1": aggregate.f1,
                "groundedness_rate": aggregate.groundedness_rate,
                "no_comment_accuracy": aggregate.no_comment_accuracy,
                "pass_at_1": aggregate.pass_at_1,
                "pass_at_2": aggregate.pass_at_2,
              }
            )

        await store.finalize_run(
          session,
          run=run,
          aggregates=aggregates,
        )
        await session.commit()

        # Metric scores make runs comparable inside Langfuse and satisfy
        # the spec's evaluation-score field without a new table.
        for aggregate in aggregates:
          for metric_name in (
            "precision",
            "recall",
            "f1",
            "groundedness_rate",
            "no_comment_accuracy",
            "pass_at_1",
            "pass_at_2",
          ):
            value = getattr(aggregate, metric_name)
            if value is None:
              continue
            score_trace(
              trace_id=trace.trace_id,
              name=f"{aggregate.system.value}.{metric_name}",
              value=value,
              comment=f"{args.dataset} split, config {args.config}",
            )

        trace.update(
          output={
            aggregate.system.value: {
              "precision": aggregate.precision,
              "recall": aggregate.recall,
              "f1": aggregate.f1,
              "pass_at_2": aggregate.pass_at_2,
            }
            for aggregate in aggregates
          }
        )

    if args.export:
      export_reports(
        Path(args.export),
        aggregates,
        per_system_examples,
        confusion,
      )

    for aggregate in aggregates:
      print(
        f"{aggregate.system.value}: "
        f"P={_fmt(aggregate.precision)} "
        f"R={_fmt(aggregate.recall)} "
        f"F1={_fmt(aggregate.f1)} "
        f"abstain={_fmt(aggregate.no_comment_accuracy)} "
        f"pass@1={_fmt(aggregate.pass_at_1)} "
        f"pass@2={_fmt(aggregate.pass_at_2)}"
      )
  finally:
    # Short-lived script: without an explicit flush, buffered spans and
    # scores never leave the process.
    flush()


if __name__ == "__main__":
  asyncio.run(main())
