# Sealed Holdout Protocol (Frozen)

Status: FROZEN 2026-08-19, before any holdout execution. Do not edit after the
holdout run begins. Corrections require abandoning the run and re-freezing.

The 72-example text holdout (`data/golden/text/holdout`) is the final report
card for this system. It has never been used for development, and this document
defines the only conditions under which it may be run.

## Preconditions (all must be true before execution)

1. Final candidate selected on the validation split, configuration frozen:
   system list, model IDs, prompt and policy versions, retrieval config,
   repair bound (max_repairs), judge model. The frozen configuration is the
   active `ReviewConfiguration` at execution time, referenced by
   `config_version`.
2. This protocol is committed to the repository before execution.
3. Human audit plan for judge rationales is ready: after the run, a human
   reviews a 20% random sample of `eval_matches.judge_rationale` rows and
   records agreement in `audited_by_human` / `human_agrees`. Disagreement rate
   above 20% invalidates the run and triggers judge review, not metric tuning.
4. Cost cap and persistence confirmed: `OPENROUTER_DAILY_COST_CAP_USD` raised
   for eval day with headroom (a full validation run costs roughly $1.1;
   holdout is 72 examples x selected systems x passes), and eval persistence
   verified on the validation path.

## Execution (exactly once)

```
uv run alembic upgrade head
uv run python -m app.evals.run \
  --dataset holdout \
  --config <fresh-label, never reused> \
  --systems baseline_a,baseline_b,final_agent \
  --max-repairs 2 \
  --export data/processed/eval_holdout_final
```

One run, one export directory, one report. No re-runs of individual examples,
no post-hoc parameter changes. If the run crashes midway, the partial run is
kept, documented as aborted with its cause, and the rerun uses a fresh config
label.

## Reporting rules

- The holdout result is a report card, not a development signal.
- Holdout metrics are never used to break a validation tie, tune a prompt, or
  select a configuration. Validation metrics do that; the holdout only
  confirms or refutes the validation-based selection.
- Precision against human-written gold is reported as a lower bound
  (open-world task) and is never presented as exact precision.
- The report includes: aggregate table (P, R, F1, groundedness, line validity,
  severity agreement, abstention accuracy, pass@1, pass@2) per system,
  judge-audit agreement rate, total cost, and the Langfuse trace ID of the
  eval run (seeded `eval-run-<id>`, deterministic).
- `requires_repo_context` source labels are excluded from retrieval-quality
  claims until relabeled (Phase 7B audit).

## Failure handling

If the final agent underperforms a baseline on the holdout, that result is
published as-is in the README and evaluation report. A negative result is a
result; the remedy is a new development cycle on the development split, never
a holdout re-run.
