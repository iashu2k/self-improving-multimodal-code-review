# Self-Improving Multimodal Code Review

A GitHub App that reviews pull requests with grounded, schema-validated inline comments — built as an evaluation-driven system that measures its own precision, groundedness, and reliability, then improves its prompts and policies through a controlled, human-gated feedback loop.

**Status:** Phase 2 complete — the app now publishes real inline reviews on pull requests. Next: Phase 3 (PostgreSQL persistence + repository RAG).

<p align="center">
  <em>Live review published by the bot on a sandbox PR — anchored to the exact changed line, with severity, category, and a suggested fix.</em>
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
- [Engineering Decisions](#engineering-decisions)
- [Testing](#testing)
- [Model Configuration](#model-configuration)
- [Known Limitations](#known-limitations)

---

## Vision

Most AI code-review demos are a single prompt that dumps unverifiable text onto a PR. This project is built the opposite way — **evaluation and safety first**:

1. **Grounded output only.** Every comment must point at an added line that actually exists in the diff. A deterministic validator suppresses anything else before it is ever published.
2. **Abstention is a feature.** If there is nothing worth flagging, the system posts nothing. Correct silence is measured and rewarded, just like correct detection.
3. **Bounded self-correction.** A critic loop (Phase 4) may repair a comment at most twice; failure means suppression, never posting uncertain content.
4. **Self-improvement with a gate.** Prompt and policy changes are versioned configurations that must beat the active config on a human-labeled golden PR set before promotion. No autonomous production prompt mutation.
5. **Multimodal where it matters.** Frontend PRs can be visually verified with a rendered screenshot, but vision findings must be grounded back to changed code lines before they become comments.

## What Makes This Different

| Typical demo | This project |
|---|---|
| One-shot LLM prompt | Pipeline with parser → generator → deterministic gate → publisher |
| Free-text output pasted as a comment | Strict JSON Schema structured output, Pydantic-validated |
| Trusts the model's line numbers | Parser-derived commentable lines enforced deterministically; model is given the legal line whitelist |
| No way to say "I don't know" | First-class abstention path — validated in production when all candidate comments fail the gate |
| Webhook handler does LLM calls inline | 202-ack + ARQ background jobs with commit-scoped dedup keys |
| "Self-improving" = changes its prompt | Versioned configs promoted only by passing a promotion gate on a held-out benchmark (Phase 8) |
| Text only | Optional vision analysis of rendered UI for frontend PRs (Phase 5) |

## Architecture (Current State)

End-to-end flow as of Phase 2:

```text
PR opened / synchronized on an installed repository
      │
      ▼
GitHub webhook ──► POST /api/v1/webhooks/github        app/api/routes/webhooks.py
      │            • HMAC-SHA256 verified against RAW body (constant-time compare)
      │            • filters: event=pull_request, action ∈ {opened, synchronize,
      │              reopened, ready_for_review}, not draft
      │            • enqueue job with dedup key review-{repo}-{pr}-{head_sha[:8]}
      │            • returns 202 immediately
      ▼
ARQ worker (Redis)                                     app/workers/jobs.py
      │
      ├─► GitHub App auth                              app/github/app_auth.py
      │     RS256 JWT (10-min) → installation token (cached, 1-hour, 5-min buffer)
      │
      ├─► Fetch current PR head SHA + unified diff     app/github/client.py
      │
      ├─► Parse diff                                   app/github/diff_parser.py
      │     RIGHT-side line tracking · "\ No newline" marker handling
      │     file filters (lockfiles, binaries, minified assets skipped)
      │
      ├─► LLM review (OpenRouter)                      app/llm/reviewer.py
      │     strict JSON Schema output · [line N] annotations
      │     + explicit commentable-lines whitelist in prompt
      │
      ├─► Deterministic gate                           app/agents/validator.py
      │     line must be an added diff line · no dupes · per-review/per-file caps
      │     all-invalid ⇒ clean abstention (posts nothing)
      │
      └─► Publish pending review + inline comments     app/github/client.py
            POST /repos/{o}/{r}/pulls/{n}/reviews  (event=COMMENT)
```

## Full System Roadmap

```text
[done]      Phase 0   Foundation — FastAPI skeleton, config, logging, tests
[done]      Phase 1   Local Review MVP — parser → OpenRouter → validated JSON
[done]      Phase 2   GitHub App — HMAC webhooks, async jobs, inline review publishing
[next]      Phase 3   Persistence + Repository RAG — PostgreSQL, Alembic, pgvector,
                      AST-aware chunking, hybrid retrieval, real idempotency
[planned]   Phase 4   LangGraph — router, retriever, generator, critic, max-2 repair loop
[planned]   Phase 5   Multimodal — Playwright screenshots + vision model, code-grounded UI findings
[planned]   Phase 6   Golden dataset — 100 curated PRs (seeded from public review corpora)
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
| Schemas | Pydantic v2 | One schema drives both API contract and model output contract |
| Background jobs | ARQ + Redis | Lightweight async Python worker; job-ID dedup |
| GitHub integration | GitHub App (JWT + installation tokens) | Least-privilege auth, bot identity on reviews |
| Tunnel (dev) | ngrok reserved domain | Stable webhook URL across restarts |
| Persistence | PostgreSQL + pgvector (Phase 3) | Review runs, feedback, embeddings, FTS in one store |
| Agent orchestration | LangGraph (Phase 4) | Conditional edges, bounded loops, persisted state |
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
│   │       └── webhooks.py           # POST /api/v1/webhooks/github (HMAC + routing)
│   ├── core/
│   │   ├── config.py                 # pydantic-settings; env-driven, validated
│   │   └── logging.py                # structlog JSON logging
│   ├── github/
│   │   ├── app_auth.py               # App JWT (RS256) + cached installation tokens
│   │   ├── client.py                 # diff fetch, head-SHA fetch, review publishing
│   │   ├── diff_parser.py            # unified-diff parser (commentable RIGHT lines)
│   │   ├── formatting.py             # severity/category badges + review summary
│   │   └── webhook_verifier.py       # HMAC-SHA256, constant-time comparison
│   ├── agents/
│   │   ├── schemas.py                # ReviewComment / ReviewResult contracts
│   │   └── validator.py              # deterministic comment gate
│   ├── llm/
│   │   ├── openrouter_client.py      # async client, structured outputs, smart retries
│   │   ├── reviewer.py               # review generator (pre-LangGraph)
│   │   └── prompts/
│   │       └── review.py             # system prompt with severity rubric
│   ├── workers/
│   │   ├── jobs.py                   # run_pr_review — the end-to-end pipeline
│   │   └── settings.py               # ARQ WorkerSettings
│   ├── db/                           # (Phase 3)
│   └── main.py                       # FastAPI app factory
├── scripts/
│   └── review_local.py               # CLI: git diff → review.json
├── tests/                            # 26 tests, all passing
├── data/
│   ├── raw/                          # ignored
│   ├── processed/                    # ignored review artifacts
│   └── golden_prs/                   # (Phase 6)
├── docker-compose.yml                # Redis (Postgres added in Phase 3)
├── pyproject.toml                    # uv package mode, ruff/mypy/pytest config
├── .env.example
└── docs/
```

## Setup

```bash
git clone https://github.com/iashu2k/self-improving-multimodal-code-review.git
cd self-improving-multimodal-code-review

uv sync
docker compose up -d redis

cp .env.example .env
# Edit .env — see table below
```

### Environment variables

| Variable | Purpose | Where to get it |
|---|---|---|
| `SECRET_KEY` | App secret | `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `OPENROUTER_API_KEY` | LLM access | openrouter.ai/keys |
| `OPENROUTER_REVIEW_MODEL` | Review generator model | `qwen/qwen3-coder-next` |
| `GITHUB_APP_ID` | Numeric App ID | GitHub → Developer settings → your App |
| `GITHUB_PRIVATE_KEY_PATH` | Path to `.pem` (outside repo) | App page → Generate a private key |
| `GITHUB_WEBHOOK_SECRET` | HMAC secret for webhook verification | You generate it; set on the App |
| `REDIS_URL` | Job queue | `redis://localhost:6379/0` (docker-compose) |

### GitHub App configuration

- **Permissions:** Contents (read), Metadata (read), Pull requests (read & write)
- **Events:** `pull_request`, `pull_request_review`, `pull_request_review_comment`
- **Webhook URL:** `https://<your-ngrok-domain>/api/v1/webhooks/github`
- Install the App on your test repositories only.

### Run the full stack (4 processes)

```bash
docker compose up -d redis                                  # 1. queue
uv run uvicorn app.main:app --reload                        # 2. API
uv run arq app.workers.settings.WorkerSettings              # 3. worker
ngrok http --url=<your-reserved-domain>.ngrok-free.dev 8000 # 4. tunnel
```

## Usage

### Automatic PR review (primary flow)

Open or update a PR on any repository where the App is installed. Within ~10 seconds the bot posts a review: a summary plus inline comments anchored to changed lines — or nothing at all, if every candidate comment fails validation (abstention).

### Local review CLI (no GitHub needed)

```bash
uv run python scripts/review_local.py \
  --repo-path ./some-repo \
  --base HEAD~1 \
  --head HEAD \
  --title "Refactor authentication" \
  --out data/processed/review.json
```

### Example published comment

> 🟡 **[MEDIUM · bug risk] Return type mismatch**
>
> The function signature declares a return type of float but now returns an int, which may break callers expecting float precision.
>
> **Suggested fix:** Update the return type annotation to `int` or remove the `int()` cast to preserve float semantics.

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

---

## Engineering Decisions

1. **Modular monolith, not microservices.** All phases live in one deployable FastAPI app; complexity is earned, not assumed.
2. **One Pydantic schema, two consumers.** The same `ReviewResult` schema drives both the OpenRouter JSON Schema request and response validation — no parallel contracts to drift.
3. **Determinism around the model, freedom inside it.** The LLM reasons freely about *what* to flag; *where* a comment may land is enforced by the parser, and the model is told the legal set explicitly.
4. **Fail closed.** Empty, malformed, schema-invalid, or ungrounded output → retry → suppress → abstain. Nothing reaches a PR without passing every gate.
5. **Model as configuration, not commitment.** Models live in `.env` aliases per role; adoption requires passing a consecutive-run structured-output check (≥9/10 valid with retry recovery).
6. **Retry transient, fail fast on permanent.** 429/5xx/malformed-JSON retry; 404 and validation-fatal errors don't.
7. **Webhooks ack fast, work async.** 202 within milliseconds; LLM latency lives in the worker. Dedup keys make GitHub retries harmless.
8. **Errors must carry evidence.** API errors include response bodies and outgoing payloads; suppressed comments are logged with reasons. Debugging from data, not guesses.
9. **Document the journey.** Model reliability findings, parser bugs, and auth pitfalls are recorded here as decisions — production realism, not tutorial code.

## Testing

```bash
uv run pytest    # 26 tests, all passing
```

| Suite | Coverage |
|---|---|
| `test_diff_parser.py` | Hunk parsing, commentable-line sets, `\ No newline` marker regression |
| `test_reviewer.py` | Reviewer with mocked client → validated `ReviewResult` |
| `test_validator.py` | Accept added line; reject context line / unknown file / duplicate / caps |
| `test_openrouter_client.py` | Structured-output error semantics |
| `test_webhooks.py` | Valid/invalid/missing/malformed signatures, tampered body, ping events, draft-PR skip, job enqueue args |
| `test_formatting.py` | Severity badges, suggested-fix rendering |
| `test_jobs.py` | End-to-end job with faked auth/GitHub/LLM → published review payload |
| `test_health.py` | API smoke tests |

## Model Configuration

| Role | Model | Phase |
|---|---|---|
| Review generator | `qwen/qwen3-coder-next` ✅ (5/5 structured runs) | 1–2 |
| Critic / QA | TBD (benchmark vs. reviewer model) | 4 |
| Vision analyzer | TBD (vision-capable, structured-output) | 5 |
| Eval judge | TBD (lowest-cost structured-output model) | 7 |

## Known Limitations

Honest list — each has a phase assigned:

- **Failed-job retry:** ARQ dedup keys block re-enqueue of a failed job for up to an hour. Proper retry semantics arrive with the `review_runs` table (Phase 3).
- **No persistence yet:** runs, comments, and suppressions live only in logs (Phase 3).
- **Diff-only context:** reviews don't yet use surrounding repository code/tests/docs — that's the RAG phase (Phase 3), and it should improve both precision and the quality of evidence.
- **Single repo language tested:** Python so far; the parser is language-agnostic but review quality per language is unevaluated (Phase 6).
- **Quality tuning is ad hoc:** severity calibration (e.g., contract-breaking changes scoring MEDIUM vs HIGH) awaits the golden dataset + critic loop rather than prompt whack-a-mole (Phases 4, 6, 7).
- **Dev-only hosting:** ngrok + local worker; deployment topology comes in Phase 9.

## Roadmap

**Phase 3 (next):** PostgreSQL + Alembic + pgvector. Persist webhook events, review runs, comments, and suppressions; real idempotency via a unique `(repo, pr, head_sha, config_version)` constraint; AST-aware repository chunking and hybrid (vector + FTS) retrieval so reviews can cite tests and call sites outside the diff.
