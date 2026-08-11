# Self-Improving Multimodal Code Review

A GitHub App that reviews pull requests with grounded, schema-validated inline comments — built as an evaluation-driven system that measures its own precision, groundedness, and reliability, then improves its prompts and policies through a controlled, human-gated feedback loop.

**Status:** Phase 5 complete — multimodal review of frontend PRs is live: sandboxed UI rendering + Playwright screenshots + a vision model, with all visual findings grounded back to changed code lines before they become comments. Phase 6A is also complete — the golden dataset candidate pool is built (467 PR-level examples: 391 enriched from public review corpora + 76 self-built verified negatives), plus a latent diff-parser bug found and fixed along the way. Next: Phase 6B (annotation, split, dataset card). [file:1][file:26]

<p align="center">
  <em>Live RAG-grounded review: the bot retrieved <code>test_calc.py</code> — a file not in the diff — and cited its <code>test_divide_by_zero</code> expectation as evidence for a CRITICAL finding anchored to a deleted guard clause.</em>
</p>

<p align="center">
  <em>Live bounded self-correction (review-sandbox PR #5): the critic caught the generator overstating a rounding claim — "the comment doesn't acknowledge that <code>round()</code> is sometimes sufficient" — issued a repair instruction, and the repaired comment was re-judged and published at reduced severity.</em>
</p>

<p align="center">
  <em>Live multimodal review (review-sandbox-ui PRs #2–5): the bot rendered the checkout page in a mobile viewport, detected horizontal overflow, contrast issues, and misaligned UI elements, then posted CRITICAL/HIGH UI regression comments grounded to <code>CheckoutButton.module.css</code> and related files.</em>
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
- [Engineering Decisions](#engineering-decisions)
- [Testing](#testing)
- [Model Configuration](#model-configuration)
- [Known Limitations](#known-limitations)



---

## Vision

Most AI code-review demos are a single prompt that dumps unverifiable text onto a PR. This project is built the opposite way — **evaluation and safety first**: [file:26]

1. **Grounded output only.** Every comment must point at a line that actually exists in the diff. A deterministic validator suppresses anything else before it is ever published.
2. **Abstention is a feature.** If there is nothing worth flagging, the system posts nothing. Correct silence is measured and rewarded, just like correct detection.
3. **Evidence beyond the diff.** Retrieval pulls the tests, call sites, and related modules that a human reviewer would open — and the model cites them. (Phase 3B)
4. **Bounded self-correction.** A critic loop (Phase 4) may repair a comment at most twice; failure means suppression, never posting uncertain content.
5. **Self-improvement with a gate.** Prompt and policy changes are versioned configurations that must beat the active config on a human-labeled golden PR set before promotion. No autonomous production prompt mutation.
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
| Feedback is an afterthought | Every posted artifact carries 👍/👎 prompts + hidden identity markers from day one — feedback is attributable to a specific run/comment/config |
| "Self-improving" = changes its prompt | Versioned configs promoted only by passing a promotion gate on a held-out benchmark (Phase 8) |
| Text only | Optional vision analysis of rendered UI for frontend PRs (Phase 5) |

## Architecture (Current State)

End-to-end flow as of Phase 5: [file:26]

```text
PR opened / synchronized on an installed repository
      │
      ▼
GitHub webhook ──► POST /api/v1/webhooks/github        app/api/routes/webhooks.py
      │            -  HMAC-SHA256 verified against RAW body (constant-time compare)
      │            -  persist WebhookEvent (dedup via INSERT ... ON CONFLICT on
      │              github_delivery_id — safe under concurrent redeliveries)
      │            -  filters: event=pull_request, action ∈ {opened, synchronize,
      │              reopened, ready_for_review}, not draft
      │            -  enqueue job with dedup key review-{repo}-{pr}-{head_sha[:8]}
      │            -  returns 202 immediately
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
[next]      Phase 6B  Golden dataset — annotation, 60/20/20 split, dataset card
[planned]   Phase 7   Evaluation harness — precision/recall, groundedness, pass@k, baselines
[planned]   Phase 8   Closed loop — feedback, diagnoser, versioned configs, promotion gate
[planned]   Phase 9   Observability/UI — Langfuse tracing, dashboard, deployment, demo
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
| Vision model | `openai/gpt-4o-mini` (via OpenRouter) | Multimodal structured outputs for UI screenshots (Phase 5) |
| Schemas | Pydantic v2 | One schema drives both API contract and model output contract |
| Background jobs | ARQ + Redis | Lightweight async Python worker; job-ID dedup |
| GitHub integration | GitHub App (JWT + installation tokens) | Least-privilege auth, bot identity on reviews |
| Tunnel (dev) | ngrok reserved domain | Stable webhook URL across restarts |
| Persistence | PostgreSQL 16 + pgvector | Review runs, events, node transitions, comments, embeddings, FTS in one store |
| ORM / migrations | SQLAlchemy 2 (async) + Alembic | Async end-to-end; versioned schema |
| Retrieval | pgvector cosine + Postgres FTS, RRF fusion | Semantic + lexical recall without a second datastore |
| Agent orchestration | LangGraph | Conditional edges, structurally bounded loops, testable nodes |
| Browser automation | Playwright in Docker | Deterministic, viewport-true UI rendering for vision analysis |
| Observability | Langfuse (Phase 9) | Traces, prompt versions, cost/latency, evals |
| Package management | uv (package mode) | Fast, reproducible, editable-install imports everywhere |
| Quality gates | Ruff, mypy strict, pytest, pre-commit | Enforced on every commit |

## Repository Structure

```text
self-improving-multimodal-code-review/
├── app/
│   ├── api/
│   │   ├── router.py                 # API route aggregation
│   │   ├── dependencies.py           # lazy ARQ pool on app.state (test-injectable)
│   │   └── routes/
│   │       ├── health.py             # GET /api/v1/health
│   │       └── webhooks.py           # POST /api/v1/webhooks/github (HMAC + persist + enqueue)
│   ├── core/
│   │   ├── config.py                 # pydantic-settings; env-driven, validated
│   │   └── logging.py                # structlog JSON logging
│   ├── db/
│   │   ├── models/                   # WebhookEvent, ReviewRun, StoredReviewComment,
│   │   │                             # RepoContextFile, ReviewRunEvent
│   │   ├── session.py                # async engine/sessionmaker
│   │   └── types.py                  # pgvector column type (imported by migrations)
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
│   ├── eval/
│   │   └── golden_schemas.py         # GoldenExample / GoldComment contracts (Phase 6)
│   ├── llm/
│   │   ├── openrouter_client.py      # async client, structured outputs, smart retries
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
├── scripts/
│   ├── review_local.py               # CLI: git diff → review.json
│   └── golden/                       # Phase 6A dataset tooling
│       ├── harvest_candidates.py     # HF triplets → candidate pool (funnel-audited filters)
│       ├── fetch_pr_context.py       # candidates → PR-level examples via GitHub API
│       ├── fetch_negatives.py        # self-built NO_COMMENT negatives (merged, zero human feedback)
│       └── pool_stats.py             # pool composition census
├── tests/                            # 83 tests, all passing
├── data/
│   ├── raw/                          # ignored
│   ├── processed/                    # ignored review artifacts
│   └── golden_prs/                   # pool/ + candidates/ ignored; curated 100 committed (6B)
├── docker-compose.yml                # Redis + Postgres (pgvector)
├── pyproject.toml                    # uv package mode, ruff/mypy/pytest config
├── .env.example
└── docs/
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
| `OPENROUTER_REVIEW_MODEL` | Review generator model | `qwen/qwen3-coder-next` |
| `OPENROUTER_ROUTER_MODEL` | Triage router model (Phase 4) | defaults to review model if unset |
| `OPENROUTER_CRITIC_MODEL` | Critic/QA model (Phase 4) | defaults to review model if unset |
| `OPENROUTER_EMBEDDING_MODEL` | Embedding model for RAG | `openai/text-embedding-3-small` |
| `GITHUB_APP_ID` | Numeric App ID | GitHub → Developer settings → your App |
| `GITHUB_PRIVATE_KEY_PATH` | Path to `.pem` (outside repo) | App page → Generate a private key |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for webhook verification | You generate it; set on the App |
| `GITHUB_DATASET_TOKEN` | Read-only PAT for public repo data — Phase 6A scripts only, never used by the app | GitHub → Developer settings → Fine-grained tokens → Public repositories (read-only) |
| `DATABASE_URL` | Async Postgres DSN | `postgresql+asyncpg://postgres:postgres@localhost:5432/code_review` |
| `REDIS_URL` | Job queue | `redis://localhost:6379/0` (docker-compose) |

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

Posted reviews include a 👍/👎 prompt on the summary and each inline comment. Reactions are the raw feedback signal for the tuning loop (Phase 6+); hidden metadata markers on each comment make them attributable to a specific run.

### Local review CLI (no GitHub needed)

```bash
uv run python scripts/review_local.py \
  --repo-path ./some-repo \
  --base HEAD~1 \
  --head HEAD \
  --title "Refactor authentication" \
  --out data/processed/review.json
```

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

1. *Intermittent `JSONDecodeError` on `qwen/qwen3.6-35b-a3b`* — thinking-mode output consuming the token budget before final JSON. Hardened the client: retry malformed JSON/empty content (not just HTTP errors), preview raw content in errors.
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
- **Comment formatting** — severity emoji + category badge + concise body + suggested fix; branded review summary.

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

**Goal:** multmodal review of frontend PRs — Playwright screenshots of rendered UI, a vision model producing structured observations, and UI findings grounded back to changed code lines before they become comments. [file:1][file:26]

**Built:**

- **Sandbox runner + viewport capture** — `app/vision/runner.py` builds and runs a Docker image of the review-sandbox UI, and `app/vision/capture.py` drives Playwright through routes like `/checkout` in mobile/desktop viewports, saving PNG screenshots under `data/processed/...`.
- **Vision analyzer** — `app/vision/analyzer.py` calls `OPENROUTER_VISION_MODEL` (`openai/gpt-4o-mini`) with structured prompts that include PR title, diff summary, and viewport metadata; returns `VisionAnalysisResult` objects with typed observations (overflow, hidden content, contrast, alignment) per viewport.
- **Grounding + bridge** — a grounding layer maps observations back to file paths and line ranges (e.g., `src/components/CheckoutButton.module.css` lines 3–10) using heuristics plus diff context, producing `GroundedObservation` records; `app/vision/review_bridge.py` converts these into `ReviewComment` objects with `severity`, `category=ReviewCategory.UI_REGRESSION`, titles/bodies that name the component and viewport, and `evidence` citing specific CSS/TSX lines causing the issue.
- **Graph integration** — triage sets `use_vision` for frontend PRs; `run_pr_review` calls the vision analyzer and merges visual comments with text comments before publishing.

**Issues hit:**

1. **Event loop conflicts with sync wrappers** — initial `asyncio.run()`-based sync helper (`analyze_pr_visual_sync`) crashed under `pytest.mark.asyncio`. Fixed by using only async entrypoints and `await analyze_pr_visual` in `run_pr_review`.
2. **Redis loop mismatch** — global singleton Redis clients caused “Future attached to a different loop” in tests. Fixed by scoping Redis clients per event loop via `get_running_loop()` and an `lru_cache` keyed on `id(loop)`.
3. **GitHub diff media-type quirks** — early 406/404 responses when fetching PR diffs led to a simpler, robust `get_pr_diff` that uses `application/vnd.github.v3.diff` and fails closed with empty diff instead of chasing `diff_url` on `github.com`.
4. **PR size caps interfering with demos** — large PRs (including accidentally committed `node_modules`) triggered `pr_too_large` abstentions. For Phase 5 demos, caps were relaxed and the seeded review-sandbox-ui PRs kept small and focused.

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

**Result:** **467 examples** (172 bug, 89 security, 71 refactor, 59 performance, 76 negatives), 87% multi-file. Attrition all in expected classes: 97 contaminated negatives, 18 candidate-file mismatches, 3 gone repos, 1 oversized. Pool is gitignored (regenerable from the API); only the curated 100 will be committed in 6B. **Sequencing note:** 6A → Phase 5 → 6B, because the 5 multimodal golden examples require the Phase 5 demo repo (`review-sandbox-ui`).

---

## Engineering Decisions

1. **Modular monolith, not microservices.** All phases live in one deployable FastAPI app; complexity is earned, not assumed.
2. **One Pydantic schema, two consumers.** The same `ReviewResult` schema drives both the OpenRouter JSON Schema request and response validation — no parallel contracts to drift.
3. **Determinism around the model, freedom inside it.** The LLM reasons freely about *what* to flag; *where* a comment may land is enforced by the parser, and the model is told the legal set explicitly.
4. **Fail closed.** Empty, malformed, schema-invalid, or ungrounded output → retry → suppress → abstain. Nothing reaches a PR without passing every gate.
5. **Model as configuration, not commitment.** Models live in `.env` aliases per role; adoption requires passing a consecutive-run structured-output check (≥9/10 valid with retry recovery).
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
17. **Labels are derived through production code.** Golden-set line anchors are computed by the same diff parser the validator enforces with — labels and the gate can never disagree about what a legal line is. Dataset-provided line numbers (chunk-relative) are never trusted.

## Testing

```bash
uv run pytest    # 83 tests, all passing
```

| Suite | Coverage |
|---|---|
| `test_diff_parser.py` | Hunk parsing, commentable-line sets, `\ No newline` marker regression, file status/rename tracking (6A regression) |
| `test_reviewer.py` | Reviewer with mocked client → validated `ReviewResult`; commentable-line map always bound |
| `test_validator.py` | Accept added line / RIGHT context line for deletions; reject out-of-diff line / unknown file / duplicate / caps |
| `test_openrouter_client.py` | Structured-output error semantics |
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
| `test_health.py` | API smoke tests |

## Model Configuration

| Role | Model | Phase |
|---|---|---|
| Review generator | `qwen/qwen3-coder-next` ✅ (5/5 structured runs) | 1–4 |
| Embeddings | `openai/text-embedding-3-small` ✅ (via OpenRouter) | 3 |
| Triage router | `qwen/qwen3-coder-next` ✅ (`OPENROUTER_ROUTER_MODEL`) | 4 |
| Critic / QA | `qwen/qwen3-coder-next` ✅ (`OPENROUTER_CRITIC_MODEL`) — first live repair loop verified; benchmark a stronger critic later | 4 |
| Vision analyzer | TBD (vision-capable, structured-output) | 5 |
| Eval judge | TBD (lowest-cost structured-output model) | 7 |

## Known Limitations

Honest list — each has a phase assigned:

- **Feedback collection is passive:** markers and emoji prompts ship on every review, but nothing consumes reactions yet — that's the Phase 6/8 loop.
- **Index freshness is SHA-scoped:** context is indexed at the PR head SHA and reused across redeliveries — correct by construction, but a first review of a big repo pays the full indexing cost. Incremental/background indexing is a later optimization.
- **Retrieval seeding is heuristic:** queries come from diff paths + hunk keywords; triage's `review_focus` isn't yet wired into retrieval queries, and there's no query reformulation or multi-hop retrieval (a natural Phase 5/6 refinement).
- **RAG path inside the graph is unexercised live (beyond the Phase 3B demo):** the golden pool is built; labeled RAG-slice examples (incl. banking review-sandbox PR #5) land in Phase 6B annotation.
- **QA heuristics are uncalibrated:** the word-count floor and Jaccard threshold are reasonable defaults, not measured values — calibration happens on the golden development split (Phase 6B/7); the candidate pool is now built.
- **Repair fidelity vs. sycophancy unevaluated:** repaired comments can parrot the critic's instruction verbatim; high fidelity now, but it's an eval dimension, not a guarantee (Phase 7).
- **Single repo language tested:** Python so far; the parser is language-agnostic but chunking quality per language is unevaluated (Phase 6).
- **Golden pool skews:** dataset-derived positives are Python-only and older (HF corpus era); self-built negatives skew recent (2024–2026). To be documented in the Phase 6B dataset card.
- **Dev-only hosting:** ngrok + local worker; deployment topology comes in Phase 9.

## Roadmap

**Phase 5 (next):** multimodal review of frontend PRs — Playwright screenshots of rendered UI, a vision model producing structured observations, and UI findings grounded back to changed code lines before they become comments. The graph is ready for it: `ui_screenshot_url` / `vision_observations` are already in the state model, and triage already detects frontend file changes — `use_vision` is simply forced off until the analyzer node lands.
