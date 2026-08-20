# Self-Improving Multimodal Code Review

A GitHub App that reviews pull requests with grounded, schema-validated inline comments — built as an evaluation-driven system that measures its own precision, groundedness, and reliability, then improves its prompts and policies through a controlled, human-gated feedback loop.

### Pull Request Demos

These live pull requests demonstrate the system reviewing beyond a one-shot diff prompt:
- [Repository-aware review with hybrid RAG](https://github.com/iashu2k/review-sandbox/pull/5) — the reviewer retrieves relevant repository context, such as tests and call sites outside the patch, then produces a diff-anchored comment grounded in that evidence.

- [Multimodal frontend review with vision](https://github.com/iashu2k/review-sandbox-ui/pull/5) — the reviewer renders the changed UI in a sandboxed browser, analyzes mobile and desktop screenshots with a vision model, and grounds detected regressions back to the changed CSS/TSX lines.

**Status:** Phase 9 complete — review runs, eval runs, and promotion decisions are traceable through fail-open Langfuse instrumentation. A read-only dashboard API and server-rendered UI let a human gatekeeper inspect the configuration lifecycle without touching SQL, while the sealed-holdout protocol is frozen ahead of execution.

---

## Architecture

<p align="center">
  <img
    src="./assets/architecture-diagram.png"
    alt="DeepFile Agentic GraphRAG architecture showing offline ingestion, graph and hybrid retrieval, LangGraph orchestration, self-correction, RAGAS evaluation, and Langfuse observability"
    width="1400"
  />
</p>

---

## Table of Contents

- [Vision](#vision)
- [What Makes This Different](#what-makes-this-different)
- [Architecture (Current State)](#architecture-current-state)
- [Full System Roadmap](#full-system-roadmap)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Setup](#setup)
- [Usage](#usage)
- [Development Log](#development-log)
  - [Phase 0 — Foundation](#phase-0--foundation)
  - [Phase 1 — Local Review MVP](#phase-1--local-review-mvp)
  - [Phase 2A — GitHub App + Webhook Verification](#phase-2a--github-app--webhook-verification)
  - [Phase 2B — End-to-End Inline Review Publishing](#phase-2b--end-to-end-inline-review-publishing)
  - [Phase 2C — Feedback Instrumentation](#phase-2c--feedback-instrumentation)
  - [Phase 3A — PostgreSQL Persistence & Audit Trail](#phase-3a--postgresql-persistence--audit-trail)
  - [Phase 3B — AST Chunking + Hybrid Retrieval (RAG)](#phase-3b--ast-chunking--hybrid-retrieval-rag)
  - [Phase 4 — LangGraph Agent Workflow](#phase-4--langgraph-agent-workflow)
  - [Phase 5 — Multimodal Frontend Review](#phase-5--multimodal-frontend-review)
  - [Phase 6A — Golden Dataset Candidate Pool](#phase-6a--golden-dataset-candidate-pool)
  - [Phase 6B — Golden Visual Dataset + Vision Model Bake-off](#phase-6b--golden-visual-dataset--vision-model-bake-off)
  - [Phase 7A — Text Golden Dataset + Eval Harness + First Baselines](#phase-7a--text-golden-dataset--eval-harness--first-baselines)
  - [Phase 7B — Validation Audit and System Selection Update](#phase-7b--validation-audit-and-system-selection-update)
  - [Phase 8 — Closed Loop: Feedback, Diagnosis, Versioned Configs, Promotion Gate](#phase-8--closed-loop-feedback-diagnosis-versioned-configs-promotion-gate)
  - [Phase 9 — Observability and Dashboard](#phase-9--observability-and-dashboard)
- [Engineering Decisions](#engineering-decisions)
- [Testing](#testing)
- [Model Configuration](#model-configuration)
- [Known Limitations](#known-limitations)

---

## Vision

Most AI code-review demos are a single prompt that dumps unverifiable text onto a PR. This project is built the opposite way — **evaluation and safety first**:

1. **Grounded output only.** Every comment must point at a line that actually exists in the diff. A deterministic validator suppresses anything else before it is ever published.
2. **Abstention is a feature.** If there is nothing worth flagging, the system posts nothing. Correct silence is measured and rewarded, just like correct detection.
3. **Evidence beyond the diff.** Retrieval pulls the tests, call sites, and related modules that a human reviewer would open — and the model cites them. (Phase 3B)
4. **Bounded self-correction.** A critic loop (Phase 4) may repair a comment at most twice; failure means suppression, never posting uncertain content.
5. **Self-improvement with a gate.** Prompt and policy changes are versioned configurations that must beat the active config on a human-labeled golden PR set before promotion. No autonomous production prompt mutation. (Phase 8 — implemented)
6. **Multimodal where it matters.** Frontend PRs are visually verified with rendered screenshots from a sandboxed UI; vision findings must be grounded back to changed code lines before they become comments. (Phase 5)

## What Makes This Different

| Typical demo | This project |
|---|---|
| One-shot LLM prompt | LangGraph agent workflow: triage → retrieval → generation → critic/QA ⇄ bounded repair → deterministic gate → publish |
| Free-text output pasted as a comment | Strict JSON Schema structured output, Pydantic-validated |
| Trusts the model's line numbers | Parser-derived commentable lines enforced deterministically; model is given the legal line whitelist |
| No way to say "I don't know" | First-class abstention path — validated in production when all candidate comments fail the gate |
| Reviews see only the diff | Hybrid retrieval (pgvector + FTS, RRF-fused) grounds reviews in tests/call sites outside the diff; context is cited as evidence |
| "Self-correction" as a prompt instruction | Repair bound enforced **structurally** in graph routing — no model behavior can loop past 2 attempts |
| Webhook handler does LLM calls inline | 202-ack + ARQ background jobs with commit-scoped dedup keys |
| Fire-and-forget | Full Postgres audit trail: webhook events, review runs, every node transition, every comment AND every suppression with reasons; idempotent run upsert keyed on (repo, PR, head SHA) |
| Feedback is an afterthought | Every posted artifact carries 👍/👎 prompts + hidden identity markers from day one — feedback is attributable to a specific run/comment/config, and is now persisted and consumed by the Phase 8 diagnoser |
| "Self-improving" = changes its prompt | Versioned configs promoted only by a deterministic promotion gate — validation evidence vs. the active config **plus human approval** — with reject/rollback lifecycle (Phase 8) |
| Text only | Optional vision analysis of rendered UI for frontend PRs (Phase 5), model chosen by a golden-set bake-off (Phase 6B) |
| Evals on synthetic bugs | Golden text set built from **real human review comments** on real PRs, with overclaim tripwires (`must_not_claim`) and an LLM judge whose every rationale is persisted for human audit (Phase 7A) |

## Architecture (Current State)

End-to-end flow (webhook path, Phases 0–5):

```text
PR opened / synchronized on an installed repository
      │
      ▼
GitHub webhook ──► POST /api/v1/webhooks/github        app/api/routes/webhooks.py
      │            - HMAC-SHA256 verified against RAW body (constant-time compare)
      │            - persist WebhookEvent (dedup via INSERT ... ON CONFLICT on
      │              github_delivery_id — safe under concurrent redeliveries)
      │            - filters: event=pull_request, action ∈ {opened, synchronize,
      │              reopened, ready_for_review}, not draft
      │            - enqueue job with dedup key review-{repo}-{pr}-{head_sha[:8]}
      │            - returns 202 immediately
      ▼
ARQ worker (Redis)                                     app/workers/jobs.py
      │
      ├─► Upsert ReviewRun keyed on (repo, PR, head SHA)   app/db/models/
      │     completed ⇒ skip · failed/abstained ⇒ resume · new push ⇒ new run
      │
      ├─► GitHub App auth                              app/github/app_auth.py
      │     RS256 JWT (10-min) → installation token (cached, 1-hour, 5-min buffer)
      │
      ├─► Fetch current PR head SHA + unified diff     app/github/client.py
      │
      ├─► Parse diff                                   app/github/diff_parser.py
      │     RIGHT-side line tracking · "\ No newline" marker handling
      │     status tracking: added/modified/deleted/renamed (fixed 6A)
      │     file filters (lockfiles, binaries, minified, deleted skipped)
      │
      ├─► Index repo at head SHA (Phase 3B)            app/ingestion/
      │     tree fetch → AST-aware chunks (imports prepended, oversized splits)
      │     → embeddings → pgvector · upserted, reused across redeliveries
      │
      ├─► LangGraph agent workflow (Phase 4–5)         app/agents/graph.py
      │     ┌──────────────────────────────────────────────────────────┐
      │     │ triage_router     deterministic skips first (no source   │
      │     │                   changes, docs-only, oversized PR);     │
      │     │                   router model tunes strategy only       │
      │     │ rag_retriever     pgvector + FTS hybrid, RRF-fused       │
      │     │                   (bypassed when route.use_rag=false)    │
      │     │ review_generator  strict JSON Schema + 10-rule policy    │
      │     │ critic_qa         deterministic checks first (placement  │
      │     │                   via validator, evidence, thin bodies,  │
      │     │                   near-dupes), then LLM critic verdicts: │
      │     │                   accept / repair / reject per comment   │
      │     │ repair_generator  regenerates flagged comments with      │
      │     │                   critic feedback — max 2 rounds,        │
      │     │                   enforced by routing, not prompts       │
      │     │ vision_analyzer   sandboxed UI run + screenshots +       │
      │     │                   structured observations (Phase 5)      │
      │     │ vision_bridge     grounded visual observations →         │
      │     │                   ReviewComment objects (Phase 5)        │
      │     │ publisher         builds payload + 2C markers            │
      │     │                   (side-effect-free)                     │
      │     │ suppressor        clean abstention + retry-exhausted     │
      │     │                   finalization                           │
      │     └──────────────────────────────────────────────────────────┘
      │
      └─► Publish review + persist everything          app/github/client.py
            POST /repos/{o}/{r}/pulls/{n}/reviews  (event=COMMENT)
            run status · comments · suppressions → Postgres
            every node transition → review_run_events
            every summary + inline comment carries a 👍/👎 feedback prompt
            and a hidden HTML metadata marker (run/comment identity)
```

The agent graph (rendered from `build_graph().get_graph()`):

```mermaid
graph TD;
    __start__ --> triage_router;
    triage_router -.-> rag_retriever;
    triage_router -.-> review_generator;
    triage_router -.-> vision_analyzer;
    triage_router -.-> suppressor;
    rag_retriever --> review_generator;
    review_generator --> critic_qa;
    critic_qa -.-> publisher;
    critic_qa -.-> repair_generator;
    critic_qa -.-> suppressor;
    repair_generator --> critic_qa;
    vision_analyzer --> vision_bridge;
    vision_bridge --> publisher;
    publisher --> __end__;
    suppressor --> __end__;
```

The offline evaluation harness (Phase 7) runs review-system configurations against the golden set, outside the webhook path:

```text
data/golden/manifest.json (129 hashed cases: 124 text + 5 visual)
      │
      ▼
app/evals/run.py            python -m app.evals.run --dataset <split> --config <label>
      │                     systems: baseline_a (diff-only) · baseline_b (diff+RAG)
      │                     · final_agent (full graph)
      ▼
app/evals/matcher.py        layered matching: exact file → ±10 lines → category
      │                     equivalence → LLM judge (only on surviving pairs)
      ▼
app/evals/judge.py          semantic equivalence + groundedness, hunk-aware excerpts,
      │                     mandatory rationale persisted per judgment
      ▼
app/evals/metrics.py        P/R/F1 · groundedness · severity agreement (±1) ·
      │                     abstention accuracy · pass@k
      ▼
Postgres (run/example/match rows + generated comments) + export dir
(per_example.csv, failure_examples.json, baseline_vs_final.md, aggregates.json)
```

The Phase 8 closed loop turns that instrumentation into controlled self-improvement:

```text
Posted-review feedback + eval failures (persisted, run/comment-attributed)
      │
      ▼
app/diagnosis/report.py            build_diagnosis_report
      │                            typed failure clusters (category × agent_node),
      │                            each with attributable examples
      ▼
app/diagnosis/proposal.py          propose_configuration_candidate
      │                            → DRAFT ReviewConfiguration (parent_version set)
      ▼
POST /api/v1/configurations/{id}/evaluations
      │                            record validation-split metrics per config /
      │                            system / repeat — holdout split rejected (400)
      ▼
POST /api/v1/configurations/{id}/approve       human sign-off (approved_by/at)
      │
      ▼
POST /api/v1/configurations/{id}/promote       app/services/promotion.py
      │   deterministic gate, decision object {eligible, failed_conditions}:
      │   - candidate + active must both have complete validation aggregates
      │     (missing → 400, promotion is impossible without evidence)
      │   - candidate must not regress vs. active on the gated metrics
      │   - manual_approval must be present (else failed_conditions:
      │     ["manual_approval_missing"])
      ▼
eligible ⇒ candidate → ACTIVE, previous active → ROLLED_BACK
           ("Superseded by <version>") — preserved as the rollback target
      │
      ▼
POST /api/v1/configurations/rollback           restores the rolled-back config
                                               (safety reversal, not a promotion)
```

## Full System Roadmap

```text
[done]      Phase 0   Foundation — FastAPI skeleton, config, logging, tests
[done]      Phase 1   Local Review MVP — parser → OpenRouter → validated JSON
[done]      Phase 2   GitHub App — HMAC webhooks, async jobs, inline review publishing,
                      feedback instrumentation (emoji + hidden markers)
[done]      Phase 3   Persistence + Repository RAG — PostgreSQL, Alembic, pgvector,
                      AST-aware chunking, hybrid retrieval, idempotent run upsert
[done]      Phase 4   LangGraph agent workflow — triage router, RAG node, generator,
                      critic + deterministic QA, bounded repair (max 2), node-event audit
[done]      Phase 5   Multimodal — Playwright screenshots + vision model, code-grounded UI findings
[done]      Phase 6A  Golden dataset — candidate pool (467 PR-level examples)
[done]      Phase 6B  Golden dataset — 5 visual cases annotated/split/hashed/documented,
                      vision model bake-off (sonnet-4.5), comparative diff-anchored prompt
[done]      Phase 7   Evaluation harness — text golden set (124 reviewed examples),
                      layered matcher + LLM judge, baseline / RAG / final-agent validation
                      runs; final agent selected on validation
[done]      Phase 8   Closed loop — feedback persistence, diagnoser, versioned configs,
                      evaluation recording, deterministic promotion gate + human approval,
                      reject/rollback lifecycle, end-to-end demo
[done]      Phase 9   Observability + UI: Langfuse tracing (review runs, eval runs,
                      promotion decisions), read-only dashboard API + server-rendered UI,
                      sealed-holdout protocol frozen, demo assets
```

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Primary ML/LLM ecosystem |
| API framework | FastAPI | Async, typed, OpenAPI docs; ideal for webhooks + admin API |
| LLM access | OpenRouter | One endpoint, swappable models, JSON Schema structured outputs |
| Review model | `qwen/qwen3-coder-next` | Coding-agent-optimized, ~$0.12/M in + $0.80/M out, 5/5 structured-output reliability |
| Router / critic models | `qwen/qwen3-coder-next` (aliases per role) | Same reliability bar; stronger critic benchmarked later |
| Embedding model | `openai/text-embedding-3-small` (via OpenRouter) | 1536-dim, cheap, good enough for repo-scale retrieval |
| Vision model | `anthropic/claude-sonnet-4.5` (via OpenRouter) | Won the Phase 6B comparative bake-off: 5/5 on the golden visual suite with the diff-anchored BEFORE/AFTER prompt |
| Eval baseline generator | `anthropic/claude-sonnet-4.5` (via OpenRouter) | Evidence-chosen for reproducible abstention and groundedness behavior |
| Eval judge | `openai/gpt-4o-mini` (via OpenRouter) | ~$0.0002/judgment; rationales persisted for human audit; stronger judge planned for headline holdout numbers |
| Schemas | Pydantic v2 | One schema drives both API contract and model output contract |
| Background jobs | ARQ + Redis | Lightweight async Python worker; job-ID dedup |
| GitHub integration | GitHub App (JWT + installation tokens) | Least-privilege auth, bot identity on reviews |
| Tunnel (dev) | ngrok reserved domain | Stable webhook URL across restarts |
| Persistence | PostgreSQL 16 + pgvector | Review runs, events, node transitions, comments, embeddings, FTS, eval runs/matches, feedback, and versioned configurations in one store |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic | Async end-to-end; versioned schema |
| Retrieval | pgvector cosine + Postgres FTS, RRF fusion | Semantic + lexical recall without a second datastore |
| Agent orchestration | LangGraph | Conditional edges, structurally bounded loops, testable nodes |
| Browser automation | Playwright in Docker | Deterministic, viewport-true UI rendering for vision analysis |
| Observability | Langfuse (Phase 9) | Fail-open traces, prompt versions, cost/latency, evals |
| Package management | uv (package mode) | Fast, reproducible, editable-install imports everywhere |
| Quality gates | Ruff, mypy strict, pytest, pre-commit | Enforced on every commit |

## Repository Structure

```text
self-improving-multimodal-code-review/
├── app/
│   ├── observability/                # Phase 9: fail-open Langfuse traces, spans, generations, scores
│   ├── api/
│   │   ├── router.py                 # API route aggregation
│   │   ├── dependencies.py           # lazy ARQ pool on app.state (test-injectable)
│   │   └── routes/
│   │       ├── health.py             # GET /api/v1/health
│   │       ├── webhooks.py           # POST /api/v1/webhooks/github (HMAC + persist + enqueue)
│   │       ├── configurations.py     # Phase 8: CRUD + diagnosis + propose-candidate +
│   │       │                         # evaluations + approve/promote/reject/rollback
│   │       └── dashboard.py          # Phase 9: read-only dashboard API + server-rendered UI
│   ├── core/
│   │   ├── config.py                 # pydantic-settings; env-driven, validated
│   │   ├── logging.py                # structlog JSON logging
│   │   └── redis.py                  # per-event-loop Redis factory
│   ├── db/
│   │   ├── models/                   # WebhookEvent, ReviewRun, StoredReviewComment,
│   │   │                             # RepoContextFile, ReviewRunEvent, eval tables,
│   │   │                             # CommentFeedback, ReviewConfiguration + evaluations
│   │   ├── session.py                # async engine/sessionmaker
│   │   └── types.py                  # pgvector column type (imported by migrations)
│   ├── diagnosis/                    # Phase 8
│   │   ├── report.py                 # build_diagnosis_report — typed failure clusters
│   │   └── proposal.py               # propose_configuration_candidate → DRAFT config
│   ├── services/                     # Phase 8
│   │   ├── configurations.py         # configuration lifecycle + record_configuration_evaluation
│   │   └── promotion.py              # the deterministic promotion gate
│   ├── github/
│   │   ├── app_auth.py               # App JWT (RS256) + cached installation tokens
│   │   ├── client.py                 # diff fetch, head-SHA fetch, tree fetch, review publishing
│   │   ├── diff_parser.py            # unified-diff parser (commentable RIGHT lines, status tracking)
│   │   ├── formatting.py             # severity/category badges + feedback markers/prompts
│   │   └── webhook_verifier.py       # HMAC-SHA256, constant-time comparison
│   ├── ingestion/
│   │   ├── chunker.py                # AST-aware Python chunker + fixed-size fallback
│   │   ├── embeddings.py             # OpenRouter embeddings client
│   │   ├── indexer.py                # repo indexing at head SHA (upsert, reused on redelivery)
│   │   └── retriever.py              # pgvector + FTS hybrid retrieval (RRF) + context-query builder
│   ├── agents/
│   │   ├── schemas.py                # ReviewComment / ReviewResult contracts
│   │   ├── qa_schemas.py             # RouteDecision / QAVerdict / suppression-reason contracts
│   │   ├── state.py                  # ReviewGraphState (LangGraph state + reducers)
│   │   ├── triage.py                 # deterministic routing rules + policy overrides
│   │   ├── qa.py                     # deterministic content QA (pre-critic)
│   │   ├── graph.py                  # the LangGraph workflow + run_review_graph entrypoint
│   │   └── validator.py              # deterministic placement gate (hard, unchanged)
│   ├── sandbox/
│   │   └── runner.py                 # networkless Docker sandbox (Phase 5; app_subdir-generalized in 6B)
│   ├── vision/
│   │   ├── analyzer.py               # vision analyzer node (Phase 5)
│   │   ├── capture.py                # viewport-true Playwright capture (runs in-container)
│   │   ├── prompts.py                # single-shot prompt v2 + comparison prompt v4.1 (6B)
│   │   ├── review_bridge.py          # grounded observations → ReviewComment objects
│   │   └── schemas.py                # VisionResult / VisualObservation contracts
│   ├── eval/
│   │   ├── golden_schemas.py         # GoldenExample / GoldComment contracts (Phase 6A)
│   │   └── visual_schemas.py         # VisualGoldenExample + VisualGroundTruth (Phase 6B)
│   ├── evals/                        # Phase 7 offline evaluation harness
│   │   ├── run.py                    # entrypoint: systems × golden split → scored runs
│   │   ├── baselines.py              # SystemOutput contracts (baseline_a/b, final_agent)
│   │   ├── matcher.py                # layered matching: file → ±10 lines → category → judge
│   │   ├── judge.py                  # LLM equivalence + groundedness, hunk-aware excerpts
│   │   ├── metrics.py                # P/R/F1, groundedness, severity ±1, abstention, pass@k
│   │   ├── schemas.py                # CATEGORY_EQUIVALENTS, SEVERITY_LADDER, MatchRecord, …
│   │   └── store.py                  # eval run/example/match + generated-comment persistence
│   ├── llm/
│   │   ├── openrouter_client.py      # async client, structured outputs, smart retries,
│   │   │                             # schema-dialect normalization (strictify + key stripping)
│   │   ├── cost_guard.py             # fail-closed daily spend cap (Redis INCRBYFLOAT)
│   │   ├── reviewer.py               # generate_comments + review_diff (legacy wrapper)
│   │   ├── router.py                 # triage strategy model call
│   │   ├── critic.py                 # LLM critic: accept/repair/reject verdicts
│   │   └── prompts/
│   │       └── review.py             # system prompt with severity rubric
│   ├── workers/
│   │   ├── jobs.py                   # run_pr_review — webhook → graph → publish
│   │   └── settings.py               # ARQ WorkerSettings
│   └── main.py                       # FastAPI app factory
├── alembic/                          # async migrations (see dev log: pgvector convention)
├── Dockerfile.sandbox                # Playwright + Node 20 sandbox image (warm npm cache)
├── fixtures/
│   ├── demo-checkout/                # Next.js seeded-defect fixture (the Phase 5 oracle)
│   └── golden/                       # generated: defect-free template + 5 case overlays (6B)
├── scripts/
│   ├── review_local.py               # CLI: git diff → review.json
│   ├── demo_phase8_promotion.py      # Phase 8: end-to-end promotion-gate walkthrough
│   ├── golden/                       # dataset tooling
│   │   ├── harvest_candidates.py     # (6A) HF triplets → candidate pool (funnel-audited filters)
│   │   ├── fetch_pr_context.py       # (6A) candidates → PR-level examples via GitHub API
│   │   ├── fetch_negatives.py        # (6A) self-built NO_COMMENT negatives (merged, zero human feedback)
│   │   ├── pool_stats.py             # (6A) pool composition census
│   │   ├── make_visual_fixtures.py   # (6B) template + case overlays, generated not hand-written
│   │   ├── build_visual_cases.py     # (6B) materialize repos → sandbox capture → diffs + shots
│   │   ├── annotate_visual_cases.py  # (6B) derived ground-truth annotations
│   │   ├── curate_text_examples.py   # (7A) pool → golden text examples (real review comments)
│   │   ├── recurate_at_review_revision.py  # (7A) re-anchor golds at the review-time revision
│   │   ├── pre_annotate.py           # (7A) LLM drafts of gold fields (issue_summary, evidence, tripwires)
│   │   ├── review_card.py            # (7A) human/assistant review cards per example
│   │   ├── apply_review_fixes.py     # (7A) the reviewed corrections, applied idempotently
│   │   ├── probe_dataset.py          # (7A) pool/dataset probes
│   │   ├── debug_anchor.py           # (7A) anchor forensics
│   │   └── build_manifest.py         # (6B/7A) sha256 manifest + generator versions + review-stamp guard
│   └── eval/
│       └── run_visual_golden.py      # (6B/7) golden visual eval + model bake-off rig
├── tests/                            # full suite passing (uv run pytest -q)
├── data/
│   ├── raw/                          # ignored
│   ├── processed/                    # ignored review + eval artifacts
│   ├── golden/                       # golden sets (committed): visual annotations/shots/diffs,
│   │                                 # text/{development,validation,holdout}/<id>/{example.json,diff.patch},
│   │                                 # text/_excluded/ (curated-out, never manifest'd),
│   │                                 # manifest.json, DATASET_CARD.md
│   └── golden_prs/                   # pool/ + candidates/ ignored (regenerable from the API)
├── docker-compose.yml                # Redis + Postgres (pgvector)
├── pyproject.toml                    # uv package mode, ruff/mypy/pytest config
├── .env.example
└── docs/
    └── holdout_protocol.md           # Phase 9: frozen sealed-holdout protocol
```

## Setup

```bash
git clone https://github.com/iashu2k/self-improving-multimodal-code-review.git
cd self-improving-multimodal-code-review
uv sync
docker compose up -d          # redis + postgres (pgvector)

cp .env.example .env
# Edit .env — see table below

uv run alembic upgrade head
```

### Environment variables

| Variable | Purpose | Where to get it |
|---|---|---|
| `SECRET_KEY` | App secret | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OPENROUTER_API_KEY` | LLM access | openrouter.ai/keys |
| `OPENROUTER_REVIEW_MODEL` | Review generator model | `qwen/qwen3-coder-next` (production); eval baseline arm: `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_ROUTER_MODEL` | Triage router model (Phase 4) | defaults to review model if unset |
| `OPENROUTER_CRITIC_MODEL` | Critic/QA model (Phase 4) | defaults to review model if unset |
| `OPENROUTER_EMBEDDING_MODEL` | Embedding model for RAG | `openai/text-embedding-3-small` |
| `OPENROUTER_VISION_MODEL` | Vision analyzer model (Phase 5/6B) | `anthropic/claude-sonnet-4.5` |
| `OPENROUTER_DAILY_COST_CAP_USD` | Fail-closed daily spend cap | default $2 — **raise on eval days** (a full validation run is ~$1.1) |
| `GITHUB_APP_ID` | Numeric App ID | GitHub → Developer settings → your App |
| `GITHUB_PRIVATE_KEY_PATH` | Path to `.pem` (outside repo) | App page → Generate a private key |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for webhook verification | You generate it; set on the App |
| `GITHUB_DATASET_TOKEN` | Read-only PAT for public repo data — Phase 6A/7A scripts only, never used by the app | GitHub → Developer settings → Fine-grained tokens → Public repositories (read-only) |
| `DATABASE_URL` | Async Postgres DSN | `postgresql+asyncpg://postgres:postgres@localhost:5432/code_review` |
| `REDIS_URL` | Job queue | `redis://localhost:6379/0` (docker-compose) |
| `LANGFUSE_PUBLIC_KEY` | Langfuse project public key | Langfuse project settings |
| `LANGFUSE_SECRET_KEY` | Langfuse project secret key | Langfuse project settings |
| `LANGFUSE_HOST` | Langfuse endpoint | `https://us.cloud.langfuse.com` for US-hosted projects; EU default is `https://cloud.langfuse.com` |

### GitHub App configuration

- **Permissions:** Contents (read), Metadata (read), Pull requests (read & write)
- **Events:** `pull_request`, `pull_request_review`, `pull_request_review_comment`
- **Webhook URL:** `https://<your-ngrok-domain>/api/v1/webhooks/github`
- Install the App on your test repositories only.

### Run the full stack (4 processes)

```bash
docker compose up -d                                        # 1. postgres + redis
uv run uvicorn app.main:app --reload                        # 2. API
uv run arq app.workers.settings.WorkerSettings              # 3. worker
ngrok http --url=<your-reserved-domain>.ngrok-free.dev 8000 # 4. tunnel
```

## Usage

### Automatic PR review (primary flow)

Open or update a PR on any repository where the App is installed. Within ~10 seconds the bot posts a review: a summary plus inline comments anchored to changed lines — triaged, grounded in retrieved context when the route calls for it, and judged by the critic — or nothing at all, if every candidate comment fails QA (abstention). Every run, node transition, comment, and suppression is persisted for audit.

Posted reviews include a 👍/👎 prompt on the summary and each inline comment. Reactions are the raw feedback signal for the tuning loop; hidden metadata markers on each comment make them attributable to a specific run, and Phase 8 persists the normalized feedback records.

### Local review CLI (no GitHub needed)

```bash
uv run python scripts/review_local.py \
  --repo-path ./some-repo \
  --base HEAD~1 \
  --head HEAD \
  --title "Refactor authentication" \
  --out data/processed/review.json
```

### Golden visual eval (Phase 6B harness)

```bash
uv run python scripts/eval/run_visual_golden.py                                   # active vision model, 5 golden cases
uv run python scripts/eval/run_visual_golden.py --model anthropic/claude-sonnet-4.5  # bake-off any OpenRouter model
```

### Golden text eval (Phase 7 harness)

```bash
# Diff-only baseline
uv run python -m app.evals.run --dataset validation --config v7-baseline-a \
  --systems baseline_a --max-repairs 1 --export data/processed/eval_v7_baseline_a

# Diff + repository RAG baseline
uv run python -m app.evals.run --dataset validation --config v7-baseline-b \
  --systems baseline_b --max-repairs 1 --export data/processed/eval_v7_baseline_b

# Full router + RAG + critic/retry agent
uv run python -m app.evals.run --dataset validation --config v7-final-agent \
  --systems final_agent --max-repairs 2 --export data/processed/eval_v7_final_agent
```

Splits: `development` (17 — smoke/debug only, too small to rank arms), `validation` (35 — arm/config selection), `holdout` (72 — sealed; one shot for the final number). Each run needs a fresh `--config` label; exports land in `per_example.csv`, `failure_examples.json`, `baseline_vs_final.md`, and `aggregates.json`.

### Phase 8 closed-loop demo (promotion gate walkthrough)

```bash
uv run python scripts/demo_phase8_promotion.py
```

Self-contained and re-runnable against the dev database. The walkthrough:

1. Rejects stale demo candidates from previous runs (rejected, never deleted — audit preserved).
2. Seeds `v1.1` (active) and a fresh `v1.2-demo-*` draft candidate.
3. **Promote with no metrics → 400** (`candidate has no complete validation evaluation aggregate`) — unproven promotion is impossible.
4. Records 3 validation repeats for baseline and candidate via `POST /configurations/{id}/evaluations`.
5. **Promote without approval → 200 `{eligible: false, failed_conditions: ["manual_approval_missing"]}`** — the gate names exactly what is missing, and persists the decision into the candidate's `evaluation_summary`.
6. Human approval via `POST /configurations/{id}/approve` (`{"approved_by": "..."}`).
7. **Promote → `{eligible: true}`** — candidate becomes `active`; `v1.1` becomes `rolled_back` ("Superseded by …"), preserved as the rollback target.
8. `POST /configurations/rollback` restores `v1.1`, returning the demo to its start state.

### Phase 8 configuration API

| Endpoint | Purpose |
|---|---|
| `POST /api/v1/configurations` | Create a versioned configuration (starts as `draft`) |
| `GET /api/v1/configurations` | Paginated list of configurations and lifecycle states |
| `GET /api/v1/configurations/{id}/diagnosis` | Diagnosis report: typed failure clusters from feedback + eval failures |
| `POST /api/v1/configurations/propose-candidate` | Diagnosis-driven candidate proposal → persisted `draft` |
| `POST /api/v1/configurations/{id}/evaluations` | Record validation metrics for a config (201; `holdout` split → 400) |
| `POST /api/v1/configurations/{id}/approve` | Human sign-off (`{"approved_by": "..."}`) — records approval, does **not** promote |
| `POST /api/v1/configurations/{id}/promote` | Run the deterministic gate → `{eligible, failed_conditions}`; on success activates the candidate and rolls the previous active into `rolled_back` |
| `POST /api/v1/configurations/{id}/reject` | Reject a candidate with a reason (terminal, auditable) |
| `POST /api/v1/configurations/rollback` | Restore the most recent rolled-back configuration to `active` |

Configuration lifecycle: `draft → (approve) → pending → (promote, gate-passed) → active`, with `rejected` and `rolled_back` as terminal-but-queryable states. Approval and promotion are deliberately separate: neither human sign-off alone nor passing metrics alone can activate a configuration.

### Example published comment (RAG-grounded, Phase 3)

A PR removed the zero-division guard from `divide()`. The retriever pulled in `test_calc.py` — **not part of the diff** — and the model cited it:

> 🔴 **[CRITICAL · bug risk] Zero-division guard removed without test update**
>
> Removing the `b == 0` check causes Python's native `ZeroDivisionError` instead of the expected `ValueError`, breaking `test_divide_by_zero` which expects ValueError with message 'must not be zero'.
>
> **Suggested fix:** Restore the zero-division guard or update tests to expect ZeroDivisionError.

A diff-only reviewer cannot write that comment: the failing test lives outside the diff, and the `ValueError` vs `ZeroDivisionError` contract distinction is only visible in the test file.

### Example bounded repair (live, Phase 4 — review-sandbox PR #5)

A bait PR added `total_with_tax()` using `round(price + price * TAX_RATE, 2)`. The generator flagged the rounding but **overstated the claim**. The critic's verdict was `repair`, with a surgical instruction:

> "The issue is real... but since the comment doesn't acknowledge that `round()` is *sometimes* sufficient, it overstates the problem. Limit the claim to: 'Rounding only the final result may cause rounding discrepancies in edge cases; consider using Decimal for strict financial accuracy if required by business rules.'"

The repair generator regenerated the comment; the critic re-judged it `accept` at reduced severity. What was published:

> 🔵 **[LOW · performance] Rounding inconsistency in total_with_tax**
>
> Rounding only the final result may cause rounding discrepancies in edge cases; consider using Decimal for strict financial accuracy if required by business rules.
>
> **Suggested fix:** Document that round() is acceptable for basic use but Decimal is recommended for strict financial accuracy, or replace with Decimal arithmetic if required.

The same run's second candidate (the removed zero-division guard) was accepted unmodified. Full trace — `triage → generate → critique (1 accept, 1 repair) → repair → critique (accept) → publish` — persisted in `review_run_events`.

> Frontend PRs that touch routes like `/checkout` or files under `src/components` in the review-sandbox-ui are also sent through the vision pipeline: the app spins up a sandboxed Docker image of the frontend, drives Playwright through target routes in mobile/desktop viewports, captures screenshots, and passes them to the vision model. Detected issues (overflow, hidden content, contrast, alignment) are grounded to the corresponding CSS/TSX lines and merged into the published review.

---

## Development Log

The actual engineering journey — failures included, because that's where the design decisions came from.

### Phase 0 — Foundation

**Goal:** reproducible, testable skeleton; secrets out of git; contracts for every later phase.

**Built:** uv package-mode project (hatchling, `packages = ["app"]` so `app/` imports work from scripts/tests without `PYTHONPATH`); `pydantic-settings` config with env-driven model aliases; structlog JSON logging; health route; Ruff/mypy-strict/pytest/pre-commit gates.

**Issues hit:**

1. *Pylance `reportCallIssue` on required `secret_key`* — type checker can't see runtime `.env` loading. Fixed with a default + a validator that rejects the placeholder outside development.
2. *Starlette TestClient deprecation* — moved tests to `httpx.ASGITransport` + `AsyncClient`.
3. *First commit aborted by pre-commit* (`end-of-file-fixer`) — established the re-stage loop.

### Phase 1 — Local Review MVP

**Goal:** `git diff` → parsed structured diff → OpenRouter structured output → schema-validated, deterministically gated artifact.

**Built:** domain schemas (`ReviewComment`/`ReviewResult` with evidence, severity rubric, abstention); hand-rolled unified-diff parser exposing `commentable_lines` (added RIGHT-side lines); async OpenRouter client with strict JSON Schema + `provider.require_parameters`; review generator with `[line N]` prompt annotations; deterministic validator (added-line-only, dedup, per-review/per-file caps); CLI.

**The model-reliability journey:**

1. *Intermittent `JSONDecodeError` on `qwen/qwen3.6-35b-a3b`* — thinking-mode output consumed the token budget before final JSON. Hardened the client: retry malformed JSON/empty content (not just HTTP errors), preview raw content in errors.
2. *Repeated empty `message.content` on the same model* — two full retry-cycle failures. Decision: treat the route as unreliable for this workload; switch models rather than fight it. Added diagnostic logging (`finish_reason`, `has_reasoning`, `usage`).
3. *404 on `qwen/qwen3-coder`* — model ID didn't resolve. Taught the retry policy to **fail fast on permanent errors** (404) while still retrying 429/5xx/malformed output.
4. *Final: `qwen/qwen3-coder-next`* — **5/5** consecutive valid structured runs on the seeded auth-bypass fixture; correct line anchor, concise evidence, ~$0.12/M input tokens.

**Verified behavior:** correct abstention on a harmless scaffold diff (saved as golden-set negative case #1); correct detection of a seeded authentication bypass anchored to the added line; 11 tests passing.

### Phase 2A — GitHub App + Webhook Verification

**Goal:** real GitHub `pull_request` events reaching local FastAPI over a tunnel, HMAC-verified.

**Built:** GitHub App (least-privilege permissions, PR events); private key stored as a **file path** outside the repo (multiline PEM in env files is a classic silent-auth bug); `verify_signature()` — HMAC-SHA256 against the **raw request body** with `hmac.compare_digest` (re-serialized JSON breaks signatures); webhook route returning 401/202; ngrok reserved domain for a stable URL; sandbox repo (`review-sandbox`) with a seeded `int()`-truncation PR.

**Issues hit:**

1. *405 Method Not Allowed* — webhook URL was the bare ngrok domain; GitHub POSTed to `/`. Fixed with the full route path. (Confirmed tunnel/DNS/server healthy — 405 is a routing answer, not a connectivity failure.)
2. *500 after verification passed* — structlog collision: `logger.info("webhook_received", event=...)` passes `event` twice (the positional arg IS the event name). Renamed kwarg to `github_event`; the success-path test `test_valid_signature_is_accepted` was written to catch exactly this class of regression.

### Phase 2B — End-to-End Inline Review Publishing

**Goal:** PR event → background job → App auth → diff fetch → Phase 1 pipeline → **real review on the PR**.

**Built:**

- **Two-legged GitHub App auth** — RS256 JWT (iat −60s skew, 10-min exp) exchanged for installation tokens, cached with a 5-minute expiry buffer.
- **`GitHubClient`** — PR diff via the `application/vnd.github.v3.diff` media type, current-head-SHA fetch, single-call review creation with inline comment array (`{path, line, side: RIGHT, body}`).
- **ARQ worker** — `run_pr_review` job: filter non-reviewable files (lockfiles, minified, binaries, deleted files) → review → validate → publish; 202-ack webhook keeps LLM latency out of GitHub's webhook timeout.
- **Idempotency layer 1** — job dedup key `review-{repo}-{pr}-{head_sha[:8]}`: same-commit redeliveries can't double-post; new pushes get fresh reviews.
- **Comment formatting** — severity emoji + category badge + concise body + suggested fix.

**Issues hit (each one found by a layer of defense):**

1. *Redeliveries silently not running* — first job-id scheme used the GitHub delivery ID; after a failure, ARQ's retained job record blocked re-enqueue for an hour. Fix: commit-scoped dedup keys (+ documented limitation: failed-job retry needs Phase 3's `review_runs` table for proper semantics).
2. *422 "Line could not be resolved"* — GitHub's error body was being discarded by `raise_for_status()`. Added `GitHubAPIError` carrying the full response text **and** the outgoing payload — the single most useful debugging change of the phase.
3. *Validator-caught abstention* — the model anchored a comment to a non-added line; the gate suppressed all candidates and the system abstained cleanly instead of posting garbage. Exactly what the gate is for.
4. **Root cause of #2 and #3:** `\ No newline at end of file` markers in GitHub's API diff were parsed as *context lines*, shifting every subsequent line number by one. The model was right; our arithmetic was wrong. Fixed by skipping `\`-prefixed marker lines without touching counters, with a regression test using the real-world fixture. **Lesson:** test parsers against the actual API diff format, not only hand-written fixtures.
5. *Prompt hardening from evidence* — after the suppression event, the model now receives an explicit commentable-lines whitelist map alongside inline `[line N]` annotations. Subsequent run: correct anchor (line 3), correct category (bug risk), correctly identified the float→int contract break.
6. **Proactive correctness fix** — review comments now use the **current** head SHA fetched from the API at job time, not the webhook payload's (stale after force-pushes).

**Result:** review published on `review-sandbox` PR #1 — `review_id=4889599633`, 1 inline comment, correctly anchored. 23 → 26 tests passing.

### Phase 2C — Feedback Instrumentation

**Goal:** every posted artifact is feedback-addressable, so Phase 6/8 can attribute human 👍/👎 signals back to a specific run, comment, and prompt config.

**Built:** hidden HTML metadata markers embedded in each review summary and inline comment body (`<!-- review-forge {...} -->` — invisible in rendered Markdown, parseable via the API); 👍/👎 reaction prompts appended to summaries and comments; formatting tests covering marker rendering. The App already subscribes to `pull_request_review` / `pull_request_review_comment` events, so reactions can be collected without new permissions.

**Design constraint:** markers must survive GitHub's Markdown rendering untouched (HTML comments do; front-matter and footnote tricks don't reliably) and must not pollute the visible review — reviewers should never see the instrumentation.

**Verified live (Phase 4):** markers confirmed present via the API and invisible in rendered reviews — e.g. `<!-- review-forge {"file":"calc.py","line":2,"run_id":8} -->` on review-sandbox PR #5.

### Phase 3A — PostgreSQL Persistence & Audit Trail

**Goal:** every review has a durable, queryable history — and retries are idempotent for real, not best-effort.

**Built:**

- **Four-table schema** (async SQLAlchemy + Alembic): `webhook_events` (status transitions `received → queued → processing → completed | failed`, full payload, error text), `review_runs` (one row per `(repo, pr_number, head_sha)`, status/attempts/token usage/abstain reason), `review_comments` (accepted AND suppressed, with `suppression_reason` — the gate's audit trail), `repo_context_files` (indexed file rows for 3B).
- **Concurrency-safe webhook dedup** — unique constraint on `github_delivery_id` + `INSERT … ON CONFLICT DO NOTHING`. Check-then-insert has a race window under concurrent redeliveries; the constraint doesn't.
- **Idempotent run upsert** — one `INSERT … ON CONFLICT` keyed on `(repo, pr_number, head_sha)`: completed runs are skipped, failed/abstained runs *resume* in place, a new push (new SHA) gets a fresh row. This is what makes ARQ retries and GitHub redeliveries safe.
- **Test infrastructure** — Postgres in Docker; each test runs in a transaction that's rolled back, so the suite stays fast and isolated.

**Issues hit:**

1. **`installation_id` overflow** — the events table used `Integer`; GitHub installation IDs exceed 32-bit and the first real insert crashed. Migration widened to `BigInteger`. **Lesson:** check the platform's actual ID ranges before choosing column types.
2. **Webhook dual-write transaction bug** — the route enqueued to Redis *before* committing the event row. A DB failure left a queued job pointing at a non-existent event; worse, the worker treated a missing event as a hard failure, so the poison job retried until it died. Reordered to **insert + flush (fail fast) → enqueue → commit**, and the worker now treats a missing event as a no-op. Ordering side effects around a commit boundary is a design decision, not an implementation detail.
3. **Dedup vs. retry semantics collision** — dedup originally returned `duplicate_ignored` for *any* existing delivery id, which silently ate ARQ's failure-redeliveries (the retry looked like a duplicate). Now only `completed`/`failed` deliveries dedup; stuck `processing` events are retried. Dedup and retry are the same mechanism seen from two sides — the policy has to serve both.
4. **Retry crash on unique constraint** — the worker used to `INSERT` a fresh run per attempt; attempt #2 violated the unique key. Replaced with the upsert above. **Lesson:** if a unique constraint keeps firing, it's usually telling you the write should have been an upsert.

### Phase 3B — AST Chunking + Hybrid Retrieval (RAG)

**Goal:** reviews grounded in repository context — the model can cite files *not in the diff* as evidence, the way a human reviewer opens the test file before commenting.

**Built:**

- **Repo indexing at PR head SHA** — files fetched from the GitHub tree and upserted into `repo_context_files`, keyed on `(installation_id, repo, ref_sha, file_path)`: redeliveries of the same PR reuse the index instead of re-fetching.
- **AST-aware chunking** (`ast.parse`) — top-level functions/classes kept whole with imports prepended to every chunk; oversized chunks split with overlap; leftover module-level statements grouped. Non-Python files fall back to fixed-size chunking.
- **Embeddings in pgvector** — `openai/text-embedding-3-small` (1536-dim) via OpenRouter.
- **Hybrid retrieval** — pgvector cosine similarity (`<=>`) fused with Postgres full-text search (`ts_rank_cd`) via Reciprocal Rank Fusion, seeded from diff file paths + hunk keywords. Vector alone misses exact symbol names; FTS alone misses paraphrases; RRF gets both.
- **Prompt grounding** — top-k chunks rendered into the review prompt with a hard rule: context may be cited as evidence, but comments must still target the diff.
- **Deletion-finding anchoring** — findings about *removed* code may anchor to RIGHT-side context lines (e.g., the enclosing `def`), since by definition no added line exists for a pure removal.

**Issues hit:**

1. **Alembic autogenerate can't import `Vector(1536)`** — the pgvector column type isn't importable in autogenerated migrations by default. Fixed with a project convention (`from app.db.types import *` in every migration file) rather than hand-editing one migration or dropping pgvector.
2. **Deletion-anchor policy gap** — the validator required comments to target *added* RIGHT-side lines, so a finding about a removed guard clause could never anchor anywhere: every candidate was suppressed and the run abstained (correctly, but uselessly). Policy fix + prompt steering; the model now anchors removal findings to the enclosing context line.
3. **Prompt map scoping bug** — the commentable-lines whitelist was accidentally indented into the diff-truncation branch, so it was unbound on the normal path (`UnboundLocalError`). Caught immediately by the reviewer test; one-line fix.
4. **Stale reason strings across three layers** — renaming the suppression reason (`line_not_an_added_diff_line` → `line_not_in_diff`) required touching the validator, the validator test, *and* the job-test fixture (which hardcodes the string in its fake suppressed comment). The last one was the confusing failure: the app was correct, the fixture was lying.

**Result:** on the guard-removal PR, the pipeline retrieved `test_calc.py` (not in the diff) and posted a CRITICAL comment citing `test_divide_by_zero`'s `ValueError` expectation — anchored to the enclosing `def divide(...)` context line. Run history in Postgres tells the policy-evolution story: `failed` (constraint crash) → `abstained` (anchor gap) → `published`. 33 tests passing.

### Phase 4 — LangGraph Agent Workflow

**Goal:** replace the one-shot review with a controlled, observable agent graph — deterministic-first triage → retrieval → generation → critic/QA with a **structurally bounded** repair loop → publish or clean abstention.

**Built:**

- **Triage router** — deterministic skip rules (no source changes, docs-only, oversized PRs) run with zero LLM spend; a router model (`OPENROUTER_ROUTER_MODEL`) only tunes strategy (risk level, review focus, `use_rag`) on diffs that pass — and policy overrides its output (`use_vision` forced off until Phase 5; security-sensitive paths force a security focus). The model advises; policy decides.
- **Critic & safety QA node** — deterministic checks first (placement via the existing validator, non-empty evidence, thin-body floor, fix concision, Jaccard near-duplicates), then an LLM critic returns per-comment verdicts (`accept` / `repair` / `reject` + grounded/actionable/duplicate/policy_safe flags + rationale + repair instruction). Missing verdicts fail closed to suppression.
- **Bounded repair loop** — `retry_count < 2` enforced in the graph's routing function, not in a prompt: no model behavior can loop past the cap. Candidates still flagged at the cap are suppressed `retry_exhausted` by the suppressor.
- **Side-effect-free publisher/suppressor nodes** — they build the payload (with Phase 2C attribution markers) and finalize abstentions; the worker owns the GitHub POST and DB commits. The whole graph is testable without a GitHub client.
- **`review_run_events`** — every node transition persisted (JSONB detail): the audit trail for repair loops and the raw material for the Phase 7 evaluation harness.
- **`reviewer.py` split** — `generate_comments` (prompt + LLM + schema validation, no gate) + `review_diff` as a thin legacy wrapper, so the local CLI and existing tests are untouched.
- **27 integration tests with mocked OpenRouter responses** — graph topology, routing policy, deterministic QA, verdict partitioning, repair-loop termination, payload construction, worker seam.

**Issues hit:**

1. **Rationale-marker heuristic suppressed real findings (first live run)** — a deterministic check required comment bodies to contain a hand-picked marker word (`because`/`causes`/`fails`…). It matched *my* phrasing, not the model's: two legitimate findings (`qa_no_rationale`) were killed before the critic ever ran. Fix: the deterministic layer rejects only *malformed* comments (evidence missing, body too thin, fix bloated); rationale quality is a semantic judgment and belongs to the critic. **Lesson:** keyword-level checks never decide content quality — and heuristic thresholds get calibrated on golden data (Phase 6), not vibes.
2. **Double suppression on the all-rejected path** — when deterministic QA rejected every candidate, the active set stayed in state, so the suppressor re-suppressed the same comments as `retry_exhausted`. Fixed by draining `candidate_comments` on the all-suppressed early return. Caught by the accumulated-suppressions test before it ever shipped.
3. **SQLite can't render JSONB** — the test fixture builds schema on SQLite; production is Postgres. Fixed with `JSON().with_variant(JSONB(), "postgresql")`: JSONB where it matters, JSON in tests.

**Result:** live on review-sandbox PR #5 (`run_id=8`): the generator flagged rounding in `total_with_tax` but overstated the claim; the critic repaired it (*"the comment doesn't acknowledge that round() is sometimes sufficient — it overstates the problem"*), the repaired comment was re-judged and published at reduced severity, and the division-by-zero finding was accepted unmodified. Trace: `triage_router → review_generator → critic_qa (1 accept, 1 repair) → repair_generator → critic_qa (accept) → publisher`. One observation logged for Phase 7: the repair was near-verbatim to the critic's instruction — high fidelity, but "repair sycophancy" is worth an eval dimension. 33 → 82 tests passing.

### Phase 5 — Multimodal Frontend Review

**Goal:** multimodal review of frontend PRs — Playwright screenshots of rendered UI, a vision model producing structured observations, and UI findings grounded back to changed code lines before they become comments.

**Built:**

- **Sandbox runner + viewport capture** — `app/sandbox/runner.py` runs the PR head in a networkless Docker sandbox (`--network none`, non-root user, warm npm cache, resource limits), and `app/vision/capture.py` drives Playwright through routes like `/checkout` in mobile/desktop viewports, saving viewport-true PNG screenshots (`full_page=False` + a PNG-header width assertion — the "496px lie" from a full-page capture hid a real clip during bring-up).
- **Vision analyzer** — `app/vision/analyzer.py` calls `OPENROUTER_VISION_MODEL` with structured prompts that include PR title, changed files, and viewport metadata; returns `VisionResult` objects with typed observations (overflow, hidden content, contrast, alignment) per viewport. (Phase 5 model: `openai/gpt-4o-mini`, winner of a single-shot seeded-defect bake-off — superseded in 6B, see below.)
- **Grounding + bridge** — a grounding layer maps observations back to file paths and line ranges (e.g., `src/components/CheckoutButton.module.css` lines 3–10) using heuristics plus diff context, producing `GroundedObservation` records; `app/vision/review_bridge.py` converts these into `ReviewComment` objects with `severity`, `category=ReviewCategory.UI_REGRESSION`, titles/bodies that name the component and viewport, and `evidence` citing specific CSS/TSX lines causing the issue.
- **Graph integration** — triage sets `use_vision` for frontend PRs; `run_pr_review` calls the vision analyzer and merges visual comments with text comments before publishing.
- **Cost guardrail** — every OpenRouter call passes a fail-closed daily cap (`OPENROUTER_DAILY_COST_CAP_USD`, default $2, Redis `INCRBYFLOAT` per UTC day); cost recording after each call is best-effort but loud, billing the provider-reported model.

**Issues hit:**

1. **Event loop conflicts with sync wrappers** — initial `asyncio.run()`-based sync helper (`analyze_pr_visual_sync`) crashed under `pytest.mark.asyncio`. Fixed by using only async entrypoints and `await analyze_pr_visual` in `run_pr_review`.
2. **Redis loop mismatch** — global singleton Redis clients caused “Future attached to a different loop” in tests. Fixed by scoping Redis clients per event loop via `get_running_loop()` and an `lru_cache` keyed on `id(loop)`.
3. **GitHub diff media-type quirks** — early 406/404 responses when fetching PR diffs led to a simpler, robust `get_pr_diff` that uses `application/vnd.github.v3.diff` and fails closed with empty diff instead of chasing `diff_url` on `github.com`.
4. **PR size caps interfering with demos** — large PRs (including accidentally committed `node_modules`) triggered `pr_too_large` abstentions. For Phase 5 demos, caps were relaxed and the seeded review-sandbox-ui PRs kept small and focused.
5. **Schema dialect leaks through the gateway** — OpenAI strict mode 400s without `additionalProperties: false` + full `required` lists; the client now normalizes any pydantic schema unconditionally (`strictify_schema`). Schema-valid ≠ useful, either: `qwen2.5-vl-72b` passed 10/10 schema validity while detecting nothing across 4 prompt framings — models are gated on seeded-defect usefulness, not validity.

**Result:** On review-sandbox-ui PRs (#2–5) with seeded regressions (fixed-width checkout buttons, contrast issues, misaligned headers), the system consistently:

- Captured mobile/desktop screenshots in a sandbox.
- Detected overflow and UI regressions at the correct viewport edges.
- Grounded findings to the relevant CSS/TSX files and lines.
- Posted multiple inline comments (CRITICAL/HIGH/LOW) that matched the Phase 5 handover criteria and human expectations.

### Phase 6A — Golden Dataset Candidate Pool

**Goal:** a 300+ PR-level candidate pool for the 100-example golden set (Phase 6) — seeded from public review corpora, with negatives that genuinely mean "no human found anything worth saying."

**Built:**

- **Golden schemas** (`app/eval/golden_schemas.py`) — `GoldenExample` / `GoldComment` contracts with a consistency validator (`no_comment` ⇒ empty gold comments + documented rationale). The acceptance policy is executable, not prose.
- **Candidate harvest** (`scripts/golden/harvest_candidates.py`) — HF `ronantakizawa/github-codereview` (334k triplets) → 510 candidates: Python-only, per-repo diversity cap, one candidate per PR, size caps, quality floor; **filter-funnel counters** on every gate; append-mode **security top-up** (seeded dedup, relaxed quality — annotation is the real filter for scarce categories).
- **PR-level enrichment** (`scripts/golden/fetch_pr_context.py`) — full diff + metadata + review comments via GitHub API (fine-grained read-only PAT, separate from the App). **Line anchors computed by the PRODUCTION diff parser**: `commentable_lines` / `right_side_lines` are stored per example, so labels and the validator can never disagree about legal lines. Idempotent resume (verified live), repo-scoped example IDs, redirect following, rate-limit backoff, and a failure log where every skip carries a reason.
- **Self-built negatives** (`scripts/golden/fetch_negatives.py`) — merged, human-authored, size-capped PRs with **zero human inline comments AND no human CHANGES_REQUESTED / COMMENTED-with-body review bodies**, sourced from pool repos (proven review cultures — a silent PR there means something).

**Issues hit:**

1. **Case-sensitive language filter kept 0 of 334,323 rows** — the dataset's `language` column is title-cased (`"Python"`). Fixed by normalization plus the funnel counters, so a kill-all gate can never again masquerade as "no matches" (same philosophy as persisted suppression reasons: unauditable gates can't be tuned).
2. **Latent parser bug: `status`/`old_path` were dead code.** File-header lines (`new file mode`, `deleted file mode`, `rename from`) precede the `+++` line while `current_file` is `None`, so every file reported `modified` and the worker's deleted-file filter **never fired since Phase 2B** — deleted files flowed into prompts as empty-path noise (harmless: they have no RIGHT-side lines, so the validator suppressed any anchor). Fixed with a pending-metadata stash, `reviewable_files()` applied at all three prompt-render sites (worker *and* local CLI), and a regression test mirroring real git header ordering. 82 → 83 tests.
3. **The dataset's negatives are chunk-level, not PR-level.** API verification culled 97/100: their PRs had human review feedback elsewhere. A 10-sample probe settled it: **36 human vs 1 bot comments** (deleted accounts serialize `"user": null` — treated as human, conservatively). Pivoted to self-built negatives; arguably better ground truth anyway.
4. **Review bodies ≠ inline comments.** The review-body check caught 6 merged PRs the inline-comment check alone would have mislabeled as clean negatives.
5. **CRLF normalization** — HF comments and API bodies normalize line endings differently; a whitespace-collapsing `normalize()` brought unresolved gold-comment URLs down to 7 of ~290.

**Result:** **467 examples** (172 bug, 89 security, 71 refactor, 59 performance, 76 negatives), 87% multi-file. Attrition all in expected classes: 97 contaminated negatives, 18 candidate-file mismatches, 3 gone repos, 1 oversized. Pool is gitignored (regenerable from the API); text-side curation into the golden 100 is deliberately sequenced after the visual loop (6B) proved the harness. **Sequencing note:** 6A → Phase 5 → 6B, because the 5 multimodal golden examples require the Phase 5 demo repo (`review-sandbox-ui`).

### Phase 6B — Golden Visual Dataset + Vision Model Bake-off

**Goal:** turn the Phase 5 multimodal pipeline into a measured asset — 5 annotated visual golden cases (the seeded-defect family), a versioned manifest with artifact hashes, a dataset card — and get a defensible answer to "which vision model, prompted how?"

**Built:**

- **Visual golden schemas** (`app/eval/visual_schemas.py`) — `VisualGoldenExample` extends 6A's `GoldenExample` with a `visual` block: baseline/PR shot paths, viewport, `expected_observations` (type / severity / element / edge / evidence tokens), `expected_empty`, and `ground_truth_source_line`. Matching is semantic — type equality + element named + evidence tokens cited — not string equality; empty/non-empty XOR is enforced by the model itself.
- **Generated fixtures** (`scripts/golden/make_visual_fixtures.py`) — the golden template is `fixtures/demo-checkout` *minus the seeded defect*, derived mechanically; each case overlay is one CSS transform applied with exactly-once assertions that fail loudly on fixture drift. No hand-written fixture files; one command rebuilds all five cases.
- **Case builder** (`scripts/golden/build_visual_cases.py`) — materializes baseline/PR repos, writes unified diffs, runs the Phase 5 networkless sandbox per side, collects viewport-true shots (`checkout_{mobile,desktop}_{baseline,pr}.png`).
- **Annotation generator** (`scripts/golden/annotate_visual_cases.py`) — ground truth is derived, not hand-typed: defect line numbers are located by scanning the overlay for the defect marker, so annotations stay correct under regeneration.
- **Manifest** (`scripts/golden/build_manifest.py` → `data/golden/manifest.json`) — per-case paths + sha256 of shots/diffs, split, and generator versions (vision model, prompt version, schema version). Artifact drift is a test failure.
- **Dataset card** (`data/golden/DATASET_CARD.md`) — construction method, case table, annotation schema, split rationale, known limitations, and the adversarial backlog.
- **Eval harness** (`scripts/eval/run_visual_golden.py`) — runs a vision model over the golden cases (BEFORE/AFTER shots + the diff), scores against annotations, reports over-flagging separately from misses; the `--model` flag makes it a bake-off rig. This is the Phase 7 entry point.
- **Integrity tests** (`tests/test_golden_manifest.py`) — every case file exists, hashes match, splits valid, annotations validate, gold-comment lines cite the defect marker (markers imported from the generator — single source of truth). 83 → 87 tests.

**The model bake-off (why 6B took four prompt iterations):**

| Prompt | Design | Outcome |
|---|---|---|
| v1 | Single-shot, intent-conditioned (the Phase 5 winner) | Detects the seeded overflow; confabulates on clean pages |
| v2 | v1 + anti-confabulation rules | Fabrication down; phantom "bottom-edge clipping" persists; blind to removed content |
| v3 | BEFORE/AFTER comparison | Worse — models invent BEFORE-only elements to "explain" differences |
| v4 | v3 + the actual diff in the prompt | The unlock: targeted verification of changed lines instead of free-scan |
| v4.1 | v4 + short-page rule | Final (see issue #4 below) |

| Model | v3 | v4/v4.1 | Verdict |
|---|---|---|---|
| `openai/gpt-4o-mini` | 1/5 — invented "cart items", a "promo banner", "$50.00" | — | Rejected: confabulation |
| `google/gemini-2.5-flash` | 1/5 — blind on the proven oracle; invented a checkout form | — | Rejected |
| `openai/gpt-4o` | 2/5 | 3/5 — missed white-on-white text AND alignment chaos | Rejected |
| `anthropic/claude-haiku-4.5` | (blocked by dialect bug, issue #3) | 3/5, strong diff-grounding | Runner-up |
| `anthropic/claude-sonnet-4.5` | 3/5 | **5/5** | **Winner** → `OPENROUTER_VISION_MODEL` |

Total measured spend for the entire campaign (4 prompt iterations × 4–5 models × 5 cases, plus every diagnostic re-run): **$0.34** — every call logged via `openrouter_cost` events against the $2/day cap.

**Issues hit:**

1. **Sandbox runner hardcoded the fixture path** — install/build/start did `cd fixtures/demo-checkout` and `capture.py` was resolved against the repo under test; golden case repos are bare apps. Generalized `run_pr_in_sandbox` with an `app_subdir` parameter (default preserves Phase 5 behavior byte-for-byte) and resolved `capture.py` from the project root.
2. **Latent import bug in `golden_schemas.py`** — it imported `Category` from `app.agents.schemas`, which only defines `ReviewCategory`; nothing had imported the module since 6A. One-line alias fix — and a reminder that unused modules rot invisibly.
3. **Anthropic 400 on `maxItems`** — pydantic emits `maxItems` for `max_length=5`; Anthropic structured outputs reject it (via both Azure and Bedrock routes). `strictify_schema` now strips provider-unsupported keys (`maxItems`/`minItems`/`minProperties`/`maxProperties`) unconditionally; local pydantic validation still enforces them on the parsed response. Regression test added. Same lesson as Phase 5's `additionalProperties`: normalize dialects in the adapter, unconditionally.
4. **The padding-fold phantom** — sonnet-4.5, shown a padding 24→32px diff on a short page, reported the button "clipped at the bottom edge" with confident fabricated detail — 3/3 reproductions, resistant to an explicit prompt counter-rule (v4.1's short-page rule). The diff anchor that fixed free-scan confabulation can itself trigger *reasoned* hallucination. Fix: the negative control was redesigned to be spatially neutral (button color `#2563eb → #1d4ed8`); the padding variant is preserved as a Phase 7 adversarial probe.
5. **A case-design bug caught by a model** — the first alignment defect (`margin-left: 120px` on a 320px button in a 342px content box) was arithmetically an *overflow*: haiku-4.5 reported `layout_overflow` and was correct. Measure the artifact before blaming the model — again. Redesigned as three-way alignment chaos (heading right, total center, button left).
6. **Evidence-token over-strictness** — the harness demanded substrings the model doesn't emit ("order total" vs `'orderTotal'`; "contrast" vs "too light"). Tokens retuned to model vocabulary; matching stays semantic.
7. **Hash-drift test caught a stale manifest** after artifact regeneration — pipeline discipline: fixtures → annotate → build → manifest → test; the manifest is always the last write before commit.

**Result:** **5/5** golden visual suite on `anthropic/claude-sonnet-4.5` with prompt v4.1 (diff-anchored BEFORE/AFTER comparison). Split decision: all 5 visual cases in `holdout` — the proven oracle family, n=5 too small to subdivide, and the live `demo-checkout` fixture remains the development smoke signal. Known model caveat recorded in the card: sonnet quotes plausible-but-**invented** prices in evidence prose ("$127.47" vs the real $42.00) — detection and localization are unaffected, but the grounding chain must never propagate model-quoted text verbatim without verifying it against the diff.

**Adversarial backlog (Phase 7's first self-improvement targets):** subtle intent-licensed contrast (`#d1d5db` — all models abstained), single-element centering (all models abstained), the padding-fold phantom (sonnet), invented-price quoting in evidence (sonnet).

### Phase 7A — Text Golden Dataset + Eval Harness + First Baselines

**Goal:** turn the 467-example pool into a reviewed golden text set built from **real human review comments**, get the offline eval harness (`app/evals/`) producing trustworthy numbers, and measure the first baseline.

**Built:**

- **Text golden set (124 examples)** — curated from the pool with `curate_text_examples.py`: real reviewer comments as gold, re-anchored at the review-time revision where the comment predates the PR head (`recurate_at_review_revision.py`; `anchor_basis` = `head` vs `review_comment_time`). Split-tree layout `text/{development,validation,holdout}/<id>/` (17/35/72, family-atomic per repo), ~5% `no_comment` negatives; `_excluded/` holds curated-out examples and is never read by the manifest builder.
- **LLM-drafted gold fields, human-gated** — `pre_annotate.py` drafts `issue_summary` / `evidence_requirement` / `must_not_claim` overclaim tripwires (qwen3-coder-next + claude-haiku-4.5); every draft stamped `NEEDS HUMAN REVIEW` until reviewed. Review happened in 5 assistant-led batches (~55 cards) with a human audit sample; corrections applied via `apply_review_fixes.py` (idempotent FIXES dict + bulk-accept mode). **Measured draft error rate**: 2 hallucinated rationales, 1 inverted tripwire, 1 false positive (excluded), ~30 category corrections — keyword-derived category hints were wrong ~50% of the time (`cache`→performance, `proxy`/`secret`→security), so both confusion classes were audited exhaustively. Post-audit distribution: bug_risk 39 / maintainability 41 / performance 8 / security 7 / style 5.
- **Manifest integration** — `build_manifest.py` now folds text in from the split dirs only, hard-fails on any remaining review stamp, and hashes annotations as well as diffs (label drift is now as detectable as artifact drift). 129 cases total.
- **The eval harness, debugged into honesty** — `app/evals/` (run / matcher / judge / metrics / store) existed on paper but had never run against the real dataset. Four bugs fixed before trusting a single number (below).

**Harness bugs found by the first runs (each one a metric lie):**

1. **Manifest shape mismatch** — `load_golden_split` read a planned `manifest["text"]` layout; the real manifest has `cases[]` with `kind`/`paths.annotation`. Found zero examples, loudly.
2. **Dicts vs models** — matcher expected `GoldComment` attributes; the loader passed raw JSON dicts.
3. **Judge schema placeholder** — the judge client hardcoded `json_schema={"type": "object"}`, which dies on OpenAI strict structured outputs. It had survived because nothing OpenAI-backed had ever called it (the visual runner's model isn't OpenAI). Fixed by passing the real pydantic schemas through a recursive `additionalProperties:false` strict-ifier.
4. **P=0.000 that wasn't the model** — a per-layer funnel diagnostic (10 golds → 7 pass file-match → **0 pass ±3 lines**) exposed an anchor-convention mismatch: human reviewers anchor block-ends, models anchor block-starts, and two genuine matches sat at Δ4. `LINE_TOLERANCE` 3 → 10; the judge (which sees the diff) arbitrates semantics, so a wider window costs judge calls, not precision.
5. **Groundedness 0.47 for BOTH models** — identical scores from very different models is an instrument smell, not a result: the judge was being shown `diff[:6000]` while 41% of dev diffs are longer (worst case: it saw 20% of the diff). Fixed with hunk-aware excerpts (the judge receives the hunks overlapping the judged paths/lines ±30 lines, 12k cap). Post-fix: qwen 0.565 vs sonnet **0.857** — the metric now discriminates, and that separation is the first true hallucination measurement of the project.

**The initial measurement campaign (baseline_a = diff-only, one-shot, no RAG/critic):**

| Split | Arm | P | R | Grounded | Abstain |
|---|---|---|---|---|---|
| development (10 golds) | qwen3-coder-next | 0.087 | 0.200 | 0.565 | 0.429 |
| development | claude-sonnet-4.5 | 0.000–0.105 | 0.000–0.200 | **0.857** | **1.000** |
| validation (31 golds, 2 repeats) | qwen3-coder-next | 0.049/0.050 | 0.097/0.097 | — | 0.000/0.000 |
| validation (2 repeats) | claude-sonnet-4.5 | 0.061/0.048 | 0.129/0.097 | — | **0.500/0.500** |

Dev taught a measurement law instead of a ranking: identical configs flipped the arm ranking between runs (10 golds × OpenRouter provider-routing variance at temp-0 = noise dominates; one match is 10 recall points). Arm selection moved to validation with 2 repeats per arm.

**Initial arm selected: baseline_a + `anthropic/claude-sonnet-4.5`** — F1 was statistically a coin flip (0.065–0.082 both arms), so the decision rested on metrics that replicated: abstention (0.500/0.500 vs 0.000/0.000 — qwen comments on every clean PR) and groundedness (0.857 vs 0.565).

**The strategic finding:** diff-only recall ceilings at **~10–13%** across both models, both splits, and six independent runs. ~87% of real human review comments are unreachable from the diff alone — reviewer questions, repo-context concerns, nits anchored in project knowledge. This is the core evidence for the RAG + critic thesis and the floor the contextual systems must beat. Also confirmed the open-world caveat: precision against human-written gold is a **lower bound** (valid-but-novel findings count as FP; humans comment on one thing and ignore others) — the self-improvement loop must never optimize raw precision naively.

**Cost:** full validation baseline_a run ≈ $1.05–1.15; the fail-closed $10/day cap killed one run mid-flight (working as designed — raise the cap on eval days). 87 → 98 tests.

---

## Phase 7B — Validation Audit and System Selection Update

### Audit result

Human-confirmed sample: 10 validation golds, seed 42.

| Classification | Count |
|---|---:|
| `diff_sufficient` | 6 |
| `needs_repo_context` | 3 |
| `needs_external` | 1 |

All 10 source labels had `requires_repo_context: false`.

**Conclusion:** the flag is currently unreliable; do not use it as the retrieval-addressable population metric until relabeled.

Gold-quality correction identified: `genesis-embodied-ai__genesis__pr_000961` has a mismatch between the reviewer comment (duplicate `add_weld_constraint` calls) and its `evidence_requirement` (missing `delete_weld_constraint`).

### System versions

| Version | Description |
|---|---|
| Baseline A | One-shot LLM with diff only |
| Baseline B | Diff + repository RAG |
| Final agent | Router + RAG + critic/retry + safe suppression |

### Validation results

The validation runs use the same reviewed golden set and scoring pipeline. `SystemF1`, `Recall`, `Abstain accuracy`, and `Result` are reported together so gains in detection are not confused with over-flagging or unsafe behavior.

| System | Run | F1 | Recall | Abstain accuracy | Result |
|---|---|---:|---:|---:|---|
| Baseline A | r1 | 0.082 | 0.129 | 0.500 | Historical selected baseline |
| Baseline A | r2 | 0.064 | 0.097 | 0.500 | Historical selected baseline |
| Baseline B | r1 | 0.104 | 0.161 | 0.625 | Repository RAG improves contextual recall over Baseline A |
| Baseline B | r2 | 0.098 | 0.161 | 0.625 | Improvement replicated across the second run |
| Final agent | reported run | 0.143 | 0.226 | 0.750 | Best result: router, RAG, critic/retry, and safe suppression improve F1, recall, and abstention accuracy |
| Final agent | reported run 2 | 0.137 | 0.226 | 0.750 | Near-replicated best result; maintains the same recall and abstention behavior |

**Selection:** the **final agent** is the current Phase 7 validation winner. It produces the highest F1 and recall while retaining the strongest abstention accuracy, which is the desired profile for a code-review system that must avoid low-confidence comments.

### Interpretation

- **Baseline A** establishes the diff-only floor. Its limited context yields low recall and only moderate abstention accuracy.
- **Baseline B** demonstrates that repository RAG creates a real, measurable improvement: retrieved tests, call sites, and related modules let the model identify issues that are not visible from the patch alone.
- **Final agent** adds routing, critic-guided repair, deterministic QA, and safe suppression. That combination improves useful finding recall without treating every generated comment as publishable.

The absolute scores remain appropriately modest for a real-human-comment golden set. The important result is the realistic ordering and repeated trend: **diff-only < diff + RAG < routed RAG agent with critique and suppression**.

### Operational cleanup

Marked four interrupted eval runs as `failed` rather than deleting them:

- `v1/development` ×2
- `v3-val-sonnet45-r2/validation` ×2

Reason: preserve partial audit records while preventing status-based queries and future analysis from treating them as active runs.

---

## Phase 8 — Closed Loop: Feedback, Diagnosis, Versioned Configs, Promotion Gate

**Goal:** turn the instrumentation shipped since Phase 2C (feedback markers, persisted suppressions, eval runs with judge rationales) into a controlled, human-gated improvement system: feedback → diagnosis → candidate proposal → recorded evidence → deterministic promotion gate → rollback.

**Built:**

- **Feedback persistence** (`CommentFeedback` model + migration) — normalized feedback records for inline comments and review summaries: typed labels (`false_positive`, `helpful`, …), actor type + **hashed** actor login, source (`github_reaction` / `manual_review`), source artifact IDs, attribution confidence (`exact_marker`, …), and dedup on `source_event_id` (replayed reactions can't double-count).
- **Failure taxonomy + diagnoser** (`app/diagnosis/report.py`) — `build_diagnosis_report` groups feedback records and persisted eval failures into typed clusters (category × responsible agent node), each cluster carrying its attributable examples (run/comment/example IDs, free text, judge rationales). Served at `GET /api/v1/configurations/{id}/diagnosis`. Model-assisted diagnosis stays reviewable — it never mutates policy.
- **Versioned configuration registry** (`ReviewConfiguration` + `app/services/configurations.py`) — prompt, router, critic, retrieval, threshold, model, and repair settings as explicit immutable-once-evaluated versions, with a full lifecycle: `draft → pending → active`, plus `rejected` and `rolled_back`. Approval fields (`approved_by`/`approved_at`), promotion/rejection/rollback timestamps and reasons, and an `evaluation_summary` JSONB that accumulates gate decisions. Routes: create, paginated list, approve, promote, reject, rollback.
- **Candidate proposal** (`app/diagnosis/proposal.py`) — `propose_configuration_candidate` turns a diagnosis into a persisted **draft** candidate with `parent_version` set, via `POST /api/v1/configurations/propose-candidate`. Proposals are drafts, never live changes.
- **Evaluation recording** — `POST /api/v1/configurations/{id}/evaluations` persists validation metrics (P/R/F1, groundedness, abstention, no-comment accuracy, safety failures) per configuration × system × repeat. The `holdout` split is rejected with 400 — the sealed holdout can never leak into a promotion decision.
- **The promotion gate** (`app/services/promotion.py`) — deterministic, and deliberately separate from approval:
  - Candidate **and** active config must both have complete validation aggregates for the system being promoted; missing evidence → 400 (`candidate has no complete validation evaluation aggregate`). **Promotion without evidence is impossible.**
  - The gate evaluates candidate-vs-active aggregates plus `manual_approval` and returns a **decision object** `{eligible, failed_conditions}` — ineligibility is data (e.g. `["manual_approval_missing"]`), persisted into the candidate's `evaluation_summary`, not just an error.
  - On success: candidate → `active` (`promoted_at` set); previous active → `rolled_back` with reason `"Superseded by <version>"` — that row **is** the rollback target.
- **Rollback** — `POST /api/v1/configurations/rollback` restores the most recent rolled-back configuration to `active`. Rollback is a safety reversal, not a promotion: it intentionally does not re-run the gate (the restored config already passed it once), and it comes back with `approval_status: pending_approval`.
- **End-to-end demo** (`scripts/demo_phase8_promotion.py`) — self-cleaning, re-runnable walkthrough of the whole loop against the dev database (see Usage).

**Issues hit:**

1. **Feedback model PK had no default generator (SQLite tests)** — `CommentFeedback.id` declared neither a Python-side default nor autoincrement, so every insert failed with `NOT NULL constraint failed: commentfeedback.id` under SQLite, while Postgres would have silently relied on a server default. Fixed with an explicit client-side UUID default. **Lesson:** declare key generation in the model, not the migration — tests build schema from the models.
2. **The demo assumed approval *was* the gate** — the first walkthrough asserted that approving without metrics should fail. The API disagreed: `approve` returned 200 with `status: pending`, `approval_status: approved`, `promoted_at: null`. The lifecycle deliberately separates **human sign-off** from the **deterministic gate** (`promote`). The demo — not the API — was wrong; running it against the real routes is what surfaced the actual contract. **Lesson:** exercise new lifecycle APIs end-to-end before writing their documentation.
3. **Demo state pollution across runs** — early runs left orphaned drafts, an approved-pending candidate, and eventually a *promoted* demo candidate as the active config. Rather than deleting rows, the demo now cleans up through the real lifecycle (`reject` stale candidates, `rollback` when a demo candidate is active) — audit trail intact, and the demo doubles as a reject/rollback test.
4. **ORM enum vs. string in the demo's state table** — ORM-loaded rows returned `status` as a plain string, crashing a `.value` format call. Cosmetic, but a reminder that SQLite/Postgres and flush states disagree on enum materialization; `getattr(status, "value", status)` handles both.

**Verified gate behavior (live demo output):**

| Step | Result |
|---|---|
| Promote with no recorded metrics | `400 — candidate has no complete validation evaluation aggregate` |
| Promote with metrics, no approval | `200 {eligible: false, failed_conditions: ["manual_approval_missing"]}` |
| Approve → promote | `200 {eligible: true}` — candidate `active`, v1.1 `rolled_back` |
| Rollback | v1.1 `active` again; demo returns to start state |

**Result:** the self-improvement loop is closed and human-gated end to end — feedback is attributable and persisted, failures are diagnosed into typed clusters, candidates are versioned drafts, evidence is recorded on the validation split only, and neither metrics alone nor approval alone can activate a configuration. Full suite passing (`uv run pytest -q`).

### Phase 9 — Observability and Dashboard

**Goal:** every review run, eval run, and promotion decision is traceable, and a human gatekeeper can inspect the configuration lifecycle without touching SQL.

**Built:**

- **`app/observability/` package** — fail-open Langfuse v4 client, `root_trace` with deterministic seed-derived trace IDs, `review_run_trace`, `node_span`, `llm_generation`, and `score_trace`. Deterministic IDs let the dashboard deep-link to traces without storing a mapping.
- **LangGraph and LLM instrumentation** — LangGraph node spans use the LangChain `CallbackHandler`; OpenRouter calls are wrapped as generations with usage and cost because the callback handler cannot inspect raw HTTP.
- **Traced evaluation harness** — one `eval_run` trace per run, plus `eval_system` and `eval_example` spans; aggregate metrics attach as Langfuse scores.
- **Traced Phase 8 lifecycle events** — `promotion_decision` (including `eligible` and `failed_conditions`), `configuration_rollback`, and `diagnosis_report`.
- **Read-only dashboard API** — `/api/v1/dashboard/...` serves runs, run detail, evaluation, feedback, and configurations. It introduces no new write paths.
- **Server-rendered UI** — `/dashboard/...` uses Jinja templates, no build step, and one inline stylesheet. Its rollback control calls the existing gated API.
- **Frozen holdout protocol** — `docs/holdout_protocol.md` was frozen before any holdout execution.

**Issues hit:**

1. **`propagate_attributes` moved across Langfuse releases** — it landed differently in v4: module-level rather than a client method. Fixed by pinning `langfuse>=4,<5` and importing it at module level behind a guard.
2. **Langfuse Cloud keys are region-locked** — `cloud.langfuse.com` is EU, while US projects return 401 on export until `LANGFUSE_HOST=https://us.cloud.langfuse.com` is set.
3. **Dashboard route tests reached real Postgres** — `get_db` now overrides to the per-test SQLite session, matching the existing route-test convention.

**Result:** observability is non-blocking, lifecycle decisions are inspectable in the dashboard, and the holdout protocol is locked before the final report-card run.

## Engineering Decisions

1. **Modular monolith, not microservices.** All phases live in one deployable FastAPI app; complexity is earned, not assumed.
2. **One Pydantic schema, two consumers.** The same `ReviewResult` schema drives both the OpenRouter JSON Schema request and response validation — no parallel contracts to drift.
3. **Determinism around the model, freedom inside it.** The LLM reasons freely about *what* to flag; *where* a comment may land is enforced by the parser, and the model is told the legal set explicitly.
4. **Fail closed.** Empty, malformed, schema-invalid, or ungrounded output → retry → suppress → abstain. Nothing reaches a PR without passing every gate.
5. **Model as configuration, not commitment.** Models live in `.env` aliases per role; adoption requires passing a consecutive-run structured-output check (≥9/10 valid with retry recovery) — and, for vision, a golden-set bake-off (6B).
6. **Retry transient, fail fast on permanent.** 429/5xx/malformed-JSON retry; 404 and validation-fatal errors don't.
7. **Webhooks ack fast, work async.** 202 within milliseconds; LLM latency lives in the worker. Dedup keys make GitHub retries harmless.
8. **Idempotency via constraints + upserts, not checks.** Check-then-insert races under concurrency; unique constraints with `ON CONFLICT` don't. Retries, redeliveries, and re-indexing are all safe because the schema makes them safe.
9. **Persist suppressions, not just output.** A gate you can't audit is a gate you can't tune. Every suppressed comment is stored with its reason — that's the dataset for improving the gate and, later, the prompts.
10. **Instrumentation before intelligence.** Feedback markers shipped in Phase 2C, long before anything consumes them. Retrofitting attribution onto historical reviews is impossible; emitting an invisible marker costs nothing.
11. **Hybrid retrieval in one store.** pgvector + Postgres FTS fused with RRF beats either alone (symbols vs. paraphrases) and avoids operating a second datastore.
12. **Errors must carry evidence.** API errors include response bodies and outgoing payloads; suppressed comments are logged with reasons. Debugging from data, not guesses.
13. **Document the journey.** Model reliability findings, parser bugs, transaction-ordering mistakes, and auth pitfalls are recorded here as decisions — production realism, not tutorial code.
14. **Side effects live at the edges.** Graph nodes are pure state transitions; the worker owns the GitHub POST and the DB commits. The whole agent graph is testable without a GitHub client, and worker retries can't double-post.
15. **Deterministic layers reject the malformed; models judge the semantic.** Keyword-level checks never decide content quality (a hand-rolled rationale heuristic suppressed two real findings on its first live run). Cheap deterministic gates catch structural problems; semantic judgment goes to a model with full evidence in front of it.
16. **Bounds are structural, not verbal.** The repair limit lives in the graph's routing function, not in a prompt — "max 2 attempts" can't be talked out of existence by a model.
17. **Labels are derived through production code.** Golden-set line anchors are computed by the same diff parser the validator enforces with — labels and the gate can never disagree about what a legal line is. Dataset-provided line numbers are never trusted.
18. **Regression vision is comparative, not free-scan.** Asking a vision model "is anything wrong with this page?" invites prior-driven confabulation at every capability tier; asking "what changed between BEFORE and AFTER, given this diff?" converts detection into verification. The golden set ships baseline shots for exactly this reason.
19. **Golden artifacts are generated, never hand-written.** The template equals the fixture minus the seeded defect, derived mechanically with fail-loud assertions; ground-truth line numbers are located by scanning for the defect marker. One command rebuilds the dataset; drift surfaces immediately instead of silently.
20. **Artifact drift is a test failure.** The manifest hashes every golden artifact (sha256); the pipeline order is fixtures → annotate → build → manifest → test, and the manifest is always the last write before commit.
21. **Eval instruments are suspect until they discriminate.** Two very different models scoring identically on groundedness (0.47/0.47) was judge blindness from truncated inputs, not model parity — the judge now receives the diff hunks it is judging, and the metric separated (0.57/0.86) immediately. If an instrument can't tell known-different things apart, fix the instrument before reading the number.
22. **Select on reproducible behavior, not point metrics.** At this corpus size F1 is noise (one match = 10 recall points on dev; identical configs flipped the arm ranking between runs). Abstention and groundedness replicated exactly across repeats; F1 didn't. The final system is selected only after verifying that its broader metric profile improves, not by optimizing a single score.
23. **Gold labels carry provenance and tripwires.** Text golds are LLM-drafted, assistant-reviewed, human-audited on a sample — with the measured draft error rates recorded (2 hallucinated rationales, 1 inverted tripwire, ~50% keyword-category misfires). `must_not_claim` fields make overclaiming a scored failure, and every judge rationale is persisted for a 20% human audit.
24. **Matcher layers are cheap policy; the judge is expensive semantics.** Exact file + ±10 lines + category equivalence decide *candidacy* deterministically; an LLM judge decides *equivalence* only on surviving pairs, with mandatory rationales. The judge never sees a structurally implausible pair, and no deterministic layer ever decides meaning.
25. **Approval is not promotion.** Human sign-off (`approve`) and the deterministic evidence gate (`promote`) are separate routes and separate states. Neither metrics alone nor approval alone can activate a configuration — the system requires both, by construction.
26. **Gates return decisions, not just errors.** Promotion ineligibility is a persisted decision object (`{eligible, failed_conditions}` written into the candidate's `evaluation_summary`), so a rejection is auditable data, not a lost 4xx. (Same philosophy as persisted suppression reasons, applied to configuration lifecycle.)
27. **Rollback is a safety reversal, not a promotion.** Restoring the previous configuration deliberately bypasses the gate — it already passed once — and returns with `pending_approval`. The previous active is always preserved as `rolled_back` ("Superseded by …"), never deleted.
28. **Reject, don't delete.** Stale and rejected candidates keep their rows, recorded evaluations, and reasons. Status lifecycle carries the history; deletion would destroy the audit trail the loop depends on.
29. **Observability is fail-open and never on the review critical path.**
30. **Langfuse references prompt versions; `ReviewConfiguration` remains the source of truth.**
31. **The dashboard is read-only over existing tables; mutations stay on the Phase 8 API.**
32. **Trace IDs are deterministic seeds** (`review_run` by run ID, `eval-run-<id>`, `promotion-<config id>`), so deep links need no stored mapping.

## Testing

| Suite | Coverage |
|---|---|
| `test_diff_parser.py` | Hunk parsing, commentable-line sets, `\ No newline` marker regression, file status/rename tracking (6A regression) |
| `test_reviewer.py` | Reviewer with mocked client → validated `ReviewResult`; commentable-line map always bound |
| `test_validator.py` | Accept added line / RIGHT context line for deletions; reject out-of-diff line / unknown file / duplicate / caps |
| `test_openrouter_client.py` | Structured-output error semantics; provider-dialect key stripping (`maxItems` et al., 6B) |
| `test_webhooks.py` | Valid/invalid/missing/malformed signatures, tampered body, ping events, draft-PR skip, event persistence + concurrent-safe dedup |
| `test_formatting.py` | Severity badges, suggested-fix rendering, feedback marker/prompt rendering |
| `test_chunker.py` | AST chunking: imports prepended, oversized splits, module-level grouping, non-Python fallback |
| `test_retriever.py` | Hybrid vector + FTS fusion, top-k ranking |
| `test_graph_skeleton.py` | Graph topology, bounded-retry routing policy (all four branches) |
| `test_qa_schemas.py` | RouteDecision / QAVerdict JSON contracts |
| `test_triage.py` | Deterministic skips (no LLM spend), size caps, policy overrides on model output |
| `test_retriever_node.py` | Retrieval node caps, `use_rag=false` bypass, triage→suppressor path |
| `test_generator_node.py` | Candidate population, route-focus passthrough, empty-candidate short-circuit, 10-rule policy in prompt |
| `test_qa.py` | Deterministic content QA: evidence, thin bodies, fix length, near-dupes, validator reason propagation |
| `test_critic_qa.py` | Accept/reject/repair partitioning, repair-then-accept loop, exhaustion at cap, fail-closed on missing verdicts, deterministic short-circuit |
| `test_publisher_suppressor.py` | 2C-marked payload, abstain-reason precedence, retry-exhausted finalization, audit-trail accumulation |
| `test_run_graph.py` | `run_review_graph` wrapper: state plumbing and output mapping on both terminal paths |
| `test_jobs.py` | Worker at the graph seam: published payload passthrough, idempotent run upsert, event persistence, abstention with suppressions |
| `test_golden_manifest.py` | Golden dataset integrity (6B/7A): artifact existence, sha256 drift detection, split validity, annotation schema validation, gold-line defect-marker grounding |
| `test_feedback_models.py` | (8) CommentFeedback persistence: inline-comment + summary feedback, hashed actor identity, duplicate `source_event_id` rejection |
| `test_propose_candidate_route.py` | (8) Diagnosis-driven candidate proposal → persisted draft with `parent_version`; error paths |
| `test_evaluation_recording_route.py` | (8) Evaluation recording: metric persistence per config/system/repeat, `holdout` split rejected (400), unknown config 404 |
| `test_dashboard_routes.py` | (9) Read-only dashboard runs, detail, evaluations, feedback, and configuration views; SQLite `get_db` override |
| `test_observability.py` | (9) Fail-open Langfuse guards, deterministic trace IDs, review/eval/lifecycle trace helpers |
| `test_health.py` | API smoke tests |

## Model Configuration

| Role | Model | Phase |
|---|---|---|
| Review generator | `qwen/qwen3-coder-next` ✅ (5/5 structured runs) | 1–4 |
| Embeddings | `openai/text-embedding-3-small` ✅ (via OpenRouter) | 3 |
| Triage router | `qwen/qwen3-coder-next` ✅ (`OPENROUTER_ROUTER_MODEL`) | 4 |
| Critic / QA | `qwen/qwen3-coder-next` ✅ (`OPENROUTER_CRITIC_MODEL`) — first live repair loop verified; benchmark a stronger critic later | 4 |
| Vision analyzer | `anthropic/claude-sonnet-4.5` ✅ (5/5 golden visual suite, diff-anchored comparative prompt v4.1) — replaced `openai/gpt-4o-mini` (the Phase 5 single-shot winner) after the 6B bake-off | 5–6B |
| Eval baseline generator (baseline_a) | `anthropic/claude-sonnet-4.5` ✅ — evidence-chosen for reproducible abstention and groundedness behavior | 7A |
| Eval judge | `openai/gpt-4o-mini` ✅ (default; ~$0.0002/judgment, rationales persisted for human audit) — consider a stronger judge for headline holdout numbers | 7A |

## Known Limitations

Honest list — each has a phase assigned:

- **Feedback loop is closed but not yet automated end-to-end:** feedback persists and the diagnoser clusters it, but candidate proposal is human-triggered via the API — nothing auto-opens a candidate from a failure cluster yet (a deliberate Phase 8 non-goal: no autonomous prompt mutation).
- **Promotion gate compares aggregates, not statistics:** the gate requires complete validation aggregates for candidate and active plus human approval; repeat-count minimums and significance testing are policy tuning for later, on real candidate volume.
- **Index freshness is SHA-scoped:** context is indexed at the PR head SHA and reused across redeliveries — correct by construction, but a first review of a big repo pays the full indexing cost. Incremental/background indexing is a later optimization.
- **Retrieval seeding is heuristic:** queries come from diff paths + hunk keywords; triage's `review_focus` isn't yet wired into retrieval queries, and there's no query reformulation or multi-hop retrieval.
- **QA heuristics are uncalibrated:** the word-count floor and Jaccard threshold are reasonable defaults, not measured values — calibration happens on the golden development split (Phase 7); the candidate pool is built.
- **Repair fidelity vs. sycophancy unevaluated:** repaired comments can parrot the critic's instruction verbatim; high fidelity now, but it's an eval dimension, not a guarantee (Phase 7).
- **Single repo language tested:** Python so far; the parser is language-agnostic but chunking quality per language is unevaluated (Phase 6).
- **Golden pool skews:** dataset-derived positives are Python-only and older (HF corpus era); self-built negatives skew recent (2024–2026). Documented in `data/golden/DATASET_CARD.md`.
- **Visual golden set is small and synthetic:** 5 cases, one Next.js fixture, CSS-only seeded defects, mobile-primary annotation. The adversarial backlog (subtle contrast, single-element centering, padding-fold phantom) is queued as the first self-improvement targets.
- **Vision model quotes unverified text:** sonnet-4.5 invents plausible prices/labels in evidence prose; detection and localization are reliable, verbatim quotes are not — the critic must verify quoted text against the diff before anything publishes.
- **Precision against human-written gold is a lower bound (open-world problem):** real reviewers comment on one thing and ignore others, so valid-but-novel model findings count as false positives. Measured precision understates true precision; the improvement loop weights groundedness and abstention alongside it.
- **Validation results are not the sealed headline:** the baseline/RAG/final-agent ordering is established on validation. The 72-example holdout stays sealed for the final reported number — and the Phase 8 evaluation-recording route rejects the holdout split outright.
- **Generation isn't bit-reproducible:** temp-0 over OpenRouter still varies across upstream providers; arm comparisons need repeats, and tiny metric deltas are noise.
- **Eval judge is a hardcoded CLI default:** `gpt-4o-mini` via `--judge-model`, not yet wired to settings or persisted on run records; judge quality bounds every reported metric (the 20% human rationale audit is the designed check).
- **Dev-only hosting:** ngrok + local worker; production deployment topology remains future work.

## Roadmap

**Phase 9 (complete):** observability and UI — Langfuse tracing across review runs, eval runs, and the Phase 8 configuration lifecycle; a read-only dashboard API and server-rendered UI; frozen sealed-holdout protocol; and demo assets. The holdout result remains a report card, not a development signal.
