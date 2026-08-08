# Self-Improving Multimodal Code Review

A GitHub App that reviews pull requests with grounded, schema-validated inline comments — built as an evaluation-driven system that measures its own precision, groundedness, and reliability, then improves its prompts and policies through a controlled, human-gated feedback loop.

**Status:** Phase 1 complete (Local Review MVP) — Phase 2 (GitHub App integration) in progress.

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
- [Engineering Decisions](#engineering-decisions)
- [Testing](#testing)
- [Model Configuration](#model-configuration)
- [Roadmap](#roadmap)

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
| One-shot LLM prompt | LangGraph state machine with router → RAG → generator → critic |
| Free-text output pasted as a comment | Strict JSON Schema structured output, Pydantic-validated |
| Trusts the model's line numbers | Parser-derived commentable lines enforced deterministically |
| No way to say "I don't know" | First-class abstention path with measured no-comment accuracy |
| "Self-improving" = changes its prompt | Versioned configs promoted only by passing a promotion gate on a held-out benchmark |
| Text only | Optional vision analysis of rendered UI for frontend PRs |

## Architecture (Current State)

Phase 1 delivered the local core of the pipeline:

```text
git diff (base...head)
      │
      ▼
┌─────────────────────┐
│  Unified diff parser │  app/github/diff_parser.py
│  (RIGHT-side lines)  │
└─────────┬───────────┘
          │  List[ChangedFile] with commentable_lines
          ▼
┌─────────────────────┐
│   Review generator   │  app/llm/reviewer.py
│  (OpenRouter call)   │  JSON Schema structured output
└─────────┬───────────┘
          │  raw model JSON
          ▼
┌─────────────────────┐
│  Pydantic validation │  app/agents/schemas.py (ReviewResult)
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│ Deterministic gate   │  app/agents/validator.py
│ • line must be added │  • file must be in diff
│ • no duplicate lines │  • per-review / per-file caps
└─────────┬───────────┘
          ▼
   review.json artifact
   (Phase 2: GitHub inline review)
```

## Full System Roadmap

```text
[done]      Phase 0  Foundation — FastAPI skeleton, config, logging, tests
[done]      Phase 1  Local Review MVP — parser → OpenRouter → validated JSON
[next]      Phase 2  GitHub App — HMAC webhook verification, async jobs, inline comments
[planned]   Phase 3  Repository RAG — AST-aware chunking, pgvector + Postgres FTS hybrid search
[planned]   Phase 4  LangGraph — router, retriever, generator, critic, max-2 repair loop
[planned]   Phase 5  Multimodal — Playwright screenshots + vision model, code-grounded UI findings
[planned]   Phase 6  Golden dataset — 100 curated PRs (seeded from public review corpora)
[planned]   Phase 7  Evaluation harness — precision/recall, groundedness, pass@k, baselines
[planned]   Phase 8  Closed loop — feedback, diagnoser, versioned configs, promotion gate
[planned]   Phase 9  Observability/UI — Langfuse tracing, dashboard, deployment, demo
```

## Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.12 | Primary ML/LLM ecosystem |
| API framework | FastAPI | Async, typed, OpenAPI docs; ideal for webhooks + admin API |
| LLM access | OpenRouter | One endpoint, swappable models, JSON Schema structured outputs |
| Schemas | Pydantic v2 | One schema drives both API contract and model output contract |
| Agent orchestration | LangGraph (Phase 4) | Conditional edges, bounded loops, persisted state |
| Persistence | PostgreSQL + pgvector (Phase 3) | Review runs, feedback, embeddings, FTS in one store |
| Background jobs | ARQ + Redis (Phase 2) | Lightweight async Python worker |
| Observability | Langfuse (Phase 9) | Traces, prompt versions, cost/latency, evals |
| Package management | uv | Fast, reproducible, editable-install project mode |
| Quality gates | Ruff, mypy (strict), pytest, pre-commit | Enforced on every commit |

## Repository Structure

```text
self-improving-multimodal-code-review/
├── app/
│   ├── api/
│   │   ├── router.py                 # API route aggregation
│   │   └── routes/
│   │       └── health.py             # GET /api/v1/health
│   ├── core/
│   │   ├── config.py                 # pydantic-settings; env-driven, validated
│   │   └── logging.py                # structlog JSON logging
│   ├── github/
│   │   └── diff_parser.py            # unified-diff parser (commentable RIGHT lines)
│   ├── agents/
│   │   ├── schemas.py                # ReviewComment / ReviewResult contracts
│   │   └── validator.py              # deterministic comment gate
│   ├── llm/
│   │   ├── openrouter_client.py      # async client, structured outputs, retries
│   │   ├── reviewer.py               # one-shot review generator (pre-LangGraph)
│   │   └── prompts/
│   │       └── review.py             # system prompt with severity rubric
│   ├── db/                           # (Phase 3)
│   ├── workers/                      # (Phase 2)
│   └── main.py                       # FastAPI app factory
├── scripts/
│   └── review_local.py               # CLI: git diff → review.json
├── tests/                            # 11 tests, all passing
├── data/
│   ├── raw/                          # ignored
│   ├── processed/                    # ignored review artifacts
│   └── golden_prs/                   # (Phase 6)
├── docs/
├── pyproject.toml                    # uv package mode, ruff/mypy/pytest config
├── .env.example
└── docker-compose.yml                # (Phase 2)
```

## Setup

```bash
git clone https://github.com/iashu2k/self-improving-multimodal-code-review.git
cd self-improving-multimodal-code-review

uv sync

cp .env.example .env
# Edit .env:
#   SECRET_KEY              -> python -c "import secrets; print(secrets.token_urlsafe(48))"
#   OPENROUTER_API_KEY      -> your key
#   OPENROUTER_REVIEW_MODEL -> qwen/qwen3-coder-next
```

Quality tooling:

```bash
uv run pre-commit install
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Run the API:

```bash
uv run uvicorn app.main:app --reload
# http://localhost:8000/docs
```

## Usage

### Local PR review (Phase 1 CLI)

Review any local repo between two refs:

```bash
uv run python scripts/review_local.py \
  --repo-path ./some-repo \
  --base HEAD~1 \
  --head HEAD \
  --title "Refactor authentication" \
  --body "Simplify the administrator authentication flow." \
  --out data/processed/review.json
```

Example validated output on a seeded authentication-bypass fixture:

```json
{
  "summary": "Authentication logic changed to bypass password check for admin user.",
  "comments": [
    {
      "file_path": "auth.py",
      "line": 4,
      "side": "RIGHT",
      "severity": "high",
      "category": "security",
      "title": "Authentication bypass for admin user",
      "body": "The admin user is now authenticated without validating the password, allowing any password to grant admin access.",
      "evidence": [
        "    if username == \"admin\":",
        "        return True"
      ],
      "suggested_fix": "Remove the special case for admin and require password validation for all users.",
      "confidence": 0.95
    }
  ],
  "should_post_review": true,
  "abstain_reason": null
}
```

---

## Development Log

This section records the actual engineering journey — including the failures and why specific decisions were made.

### Phase 0 — Foundation

**Goal:** a reproducible, testable skeleton that keeps secrets out of git and establishes contracts every later phase builds on.

**Built:**

- `uv init --python 3.12`, converted to **uv package mode** (`[tool.uv] package = true`, hatchling backend, `packages = ["app"]`) so `app/` is importable from `scripts/` and `tests/` without `PYTHONPATH` hacks.
- `app/core/config.py` — `pydantic-settings` loading everything from `.env`: app metadata, `SECRET_KEY`, OpenRouter credentials and model aliases, and placeholder slots for database, Redis, GitHub App, and Langfuse credentials (each lands in the phase that uses it).
  - `secret_key` carries a safe default so the type checker and local dev both pass; a model validator rejects the placeholder outside `development`.
- `app/core/logging.py` — structlog JSON logging (trace-friendly from day one).
- `GET /` and `GET /api/v1/health` with tests.
- Tooling: Ruff (E/F/I/UP/B/SIM), mypy strict, pytest + pytest-asyncio, pre-commit with ruff/ruff-format hooks.
- `.gitignore` covering `.env`, generated review artifacts under `data/`, and the local scratch fixture.

**Issues hit and fixed:**

1. *Pylance `reportCallIssue`: "Argument missing for parameter secret_key".* Type checker only sees the class signature, not runtime `.env` loading — fixed with a default plus a production-safety validator.
2. *Starlette TestClient deprecation warning* — switched tests to `httpx.ASGITransport` + `AsyncClient` (no new dependency).
3. *First commit aborted by pre-commit* (`end-of-file-fixer` modified the test file) — established the re-stage-and-recommit loop.

**Definition of done:** `uv run uvicorn app.main:app --reload` serving health checks; 2 tests green.

### Phase 1 — Local Review MVP

**Goal:** `git diff` in → parsed structured diff → OpenRouter structured output → schema-validated, deterministically gated review artifact. No GitHub App, no DB, no LangGraph yet.

**Built (in order):**

1. **Domain schemas** — `app/agents/schemas.py`
   - `Severity` (critical/high/medium/low), `ReviewCategory` (bug_risk, security, performance, maintainability, style, ui_regression) as `StrEnum`s.
   - `ReviewComment`: file path, RIGHT-side line, severity, category, title, body, **evidence (min 1)**, suggested fix, confidence.
   - `ReviewResult`: summary, comments, `should_post_review`, `abstain_reason`.
   - These contracts survive into the LangGraph phase unchanged.

2. **Unified-diff parser** — `app/github/diff_parser.py`
   - Parses `diff --git`, `---`/`+++`, `@@` hunk headers, and add/del/context lines into `ChangedFile → DiffHunk → DiffLine`.
   - Tracks old/new line numbers per line; exposes `commentable_lines` = the set of added RIGHT-side line numbers — the exact anchor GitHub's review-comment API needs.
   - Handles added/deleted/renamed files.

3. **OpenRouter client** — `app/llm/openrouter_client.py`
   - Async httpx client hitting `/api/v1/chat/completions`.
   - Strict JSON Schema structured output via `response_format: { type: "json_schema", strict: true }`.
   - `"provider": { "require_parameters": true }` so OpenRouter only routes to providers that honor the schema.
   - Tenacity retries (3 attempts, exponential backoff) for **transient** failures only: 429/5xx, empty content, malformed JSON, invalid usage objects. Permanent errors (404 model-not-found) fail fast.

4. **Review generator** — `app/llm/reviewer.py` + `app/llm/prompts/review.py`
   - `render_diff_for_prompt()` annotates every added line with `[line N]` — the single highest-leverage trick for line-number accuracy: the model copies the annotation instead of counting lines.
   - System prompt enforces: comment only on added lines, ≤2-sentence bodies, remediation only in `suggested_fix`, exact-quote evidence, max 3 comments, abstain when nothing is found, mandatory severity rubric.

5. **Deterministic validator** — `app/agents/validator.py`
   - Suppresses comments for: file not in diff, line not an added diff line, duplicate location, per-review cap (>5), per-file cap (>3).
   - The reviewer post-processes through this gate before returning; a run whose every comment fails validation converts to a clean abstention.

6. **CLI** — `scripts/review_local.py`
   - `git diff base...head` → parse → review → write `review.json`.

**Verified behavior:**

| Test | Result |
|---|---|
| Scaffold diff (no real issues) | Correct abstention: 0 comments, `should_post_review=false`, reasoned `abstain_reason` — saved as first golden-set negative case |
| Seeded auth bypass fixture | Correct detection, anchored to added line, `security` category, concise evidence + fix |
| Structured-output reliability | **5/5 consecutive valid runs** on `qwen/qwen3-coder-next` |
| Line-number accuracy | Correct on all runs (parser-derived `[line N]` annotation) |
| Unit tests | 11 passing (parser, validator, reviewer with mocked client, client error types) |

**Issues hit and fixed (the model-reliability journey):**

1. *`ModuleNotFoundError: No module named 'app'` from scripts/* — fixed by uv package mode (above).
2. *Ruff UP042* — migrated `class Severity(str, Enum)` to `StrEnum`.
3. *Test expectations wrong, not the parser* — the sample hunk's added lines are {11, 12, 13}; the test incorrectly expected {12, 13, 14}. Fixed the test; parser was correct.
4. *Intermittent `JSONDecodeError` on `qwen/qwen3.6-35b-a3b`* — root cause: reasoning/thinking output consuming the token budget before final JSON. Response: hardened the client (retry malformed JSON, not just HTTP errors; preview raw content in errors).
5. *Repeated empty `message.content` on the same model* — two full retry-cycle failures. Decision: treat the route as unreliable for this workload and change models rather than fight it. Diagnostic logging added (`finish_reason`, `has_reasoning`, `usage`) to confirm.
6. *404 on `qwen/qwen3-coder`* — model ID did not resolve on the current route. Fixed retry policy to **not** retry 404s (permanent).
7. *Final choice: `qwen/qwen3-coder-next`* — purpose-built for coding agents, ~$0.12/M input + $0.80/M output tokens, **5/5** successful structured runs.
8. *Ruff UP038 + misplaced function* — `is_retryable_exception` moved from an accidental class method to module level; `isinstance(x, A | B)` syntax applied.

---

## Engineering Decisions

1. **Modular monolith, not microservices.** All phases live in one deployable FastAPI app; complexity is earned, not assumed.
2. **One Pydantic schema, two consumers.** The same `ReviewResult` schema drives both the OpenRouter JSON Schema request and response validation — no parallel contracts to drift.
3. **Determinism around the model, freedom inside it.** The LLM may reason freely about *what* to flag, but *where* a comment may land is enforced by the parser, not trusted to the model.
4. **Fail closed.** Empty, malformed, or schema-invalid output → retry → abstain. Nothing reaches a user (or, in Phase 2, a PR) without passing every gate.
5. **Model as configuration, not commitment.** Models live in `.env` aliases (`OPENROUTER_REVIEW_MODEL`, `_CRITIC_MODEL`, `_VISION_MODEL`, `_JUDGE_MODEL`) so they can be benchmarked and swapped per role without code changes.
6. **Retry transient, fail fast on permanent.** 429/5xx/malformed-JSON retry; 404 and validation-fatal errors don't.
7. **Document the journey.** Model reliability findings are recorded here as engineering decisions — this is production realism, not tutorial code.

## Testing

```bash
uv run pytest
```

```text
tests/test_diff_parser.py       hunk parsing, commentable-line sets, context/del exclusion
tests/test_reviewer.py          reviewer with mocked client → validated ReviewResult
tests/test_validator.py         accept on added line; reject context line / unknown file / duplicate
tests/test_openrouter_client.py structured-output error semantics
tests/test_health.py            API smoke tests
```

**11 tests, all passing.** mypy strict and Ruff enforced via pre-commit on every commit.

## Model Configuration

| Role | Model | Phase |
|---|---|---|
| Review generator | `qwen/qwen3-coder-next` | 1 ✔ |
| Critic / QA | TBD (benchmark vs. reviewer model) | 4 |
| Vision analyzer | TBD (vision-capable, structured-output) | 5 |
| Eval judge | TBD (lowest-cost structured-output model) | 7 |

Reliability policy: a model is only adopted for a role after passing a consecutive-run structured-output check on representative fixtures (≥9/10 valid with automatic retry recovery).

## Roadmap

**Phase 2 (next):** GitHub App registration, `X-Hub-Signature-256` verification, `pull_request` webhook intake with idempotency, ARQ background jobs, and inline review publishing via pending-review flow on a public test repository.

Then: repository RAG (3) → LangGraph agent graph (4) → multimodal UI verification (5) → 100-PR golden dataset (6) → evaluation harness (7) → closed-loop promotion gate (8) → observability, dashboard, deployment, demo (9).
