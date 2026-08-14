"""Curate the 467-pool into the golden text set with indexed snapshots.


Usage:
  uv run python scripts/golden/curate_text_examples.py --count 3 --strategy first  # smoke
  uv run python scripts/golden/curate_text_examples.py --count 135                 # balanced


Pool layout (Phase 6A, fetch_pr_context.py):
  data/golden_prs/pool/<example_id>/
    diff.patch       full PR-level unified diff (fetched at current head)
    metadata.json    example_id, repository, pr_number, commit_sha, title,
                     description, changed_files, candidate_file,
                     commentable_lines, right_side_lines (both computed by
                     the PRODUCTION diff parser), category_hint,
                     gold_comment_url
    source_row.json  reviewer_comment, comment_line, diff_context, ...


ANCHOR DOCTRINE (from 6A, enforced here): the gold line anchor comes from
the GitHub API's review-comment object (`line`, falling back to
`original_line` for outdated comments), NEVER from the dataset's
comment_line — that field is relative to the HF dataset's fixed 51-line
code windows (before_lines/after_lines=51, comment_line=26 is the window
CENTER, a dataset constant), and the dataset's diff_context predates the
fetched diff (observed: hunk +36,130 in dataset vs +36,100 at head).
The API-derived anchor is sanity-checked against metadata's parser-computed
line sets; mismatches are recorded as failures, not silently accepted.
(First-run census: 13/100 anchors stale — the comment is outdated relative
to head and its line exists only in a diff that no longer exists.)


SELECTION: --strategy balanced samples evenly across classes
(bug/security/refactor/performance/negative) so per-category precision/
recall has signal; --strategy first takes the first N alphabetically.
SPLITS are family-atomic by repository via sha256(repo) bucketing —
deterministic and COUNT-INDEPENDENT, so backfilling with a larger --count
never moves an existing example to a different split. Targets are
approximate (20/50/30); the printed census shows actuals.


GoldComment fields needing human judgment (evidence_requirement,
must_not_claim, rationale, severity) are TODO-marked — the example fails
GoldenExample validation until annotated.


Failures append to data/golden/text/curation_failures.jsonl (dedupe by
example_id when reading; re-runs re-record persistent failures).
Requires GITHUB_DATASET_TOKEN (declared in app.core.config.settings).
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import ssl
import time
import warnings
from collections import defaultdict
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.db.session import get_session_maker
from app.github.client import GitHubClient
from app.ingestion.indexer import get_or_create_snapshot, index_snapshot
from app.llm.openrouter_client import OpenRouterClient

# SyntaxWarnings come from ast.parse inside the chunker parsing THIRD-PARTY
# repo files with sloppy escape sequences — not from this codebase.
warnings.filterwarnings("ignore", category=SyntaxWarning)


GITHUB_API = "https://api.github.com"
POOL_ROOT = Path("data/golden_prs/pool")
GOLDEN_TEXT_ROOT = Path("data/golden/text")
FAILURES = GOLDEN_TEXT_ROOT / "curation_failures.jsonl"


CLASSES = ["bug", "security", "refactor", "performance", "negative"]
MAX_RETRIES = 3


# Pool category_hint -> ReviewCategory value. Verified complete against the
# pool: vocabulary is exactly {bug, security, refactor, performance, null}.
CATEGORY_MAP = {
  "bug": "bug_risk",
  "security": "security",
  "refactor": "maintainability",
  "performance": "performance",
}


def normalize(text: str) -> str:
  """Same normalization as 6A fetch_pr_context.py: HF comment bodies and
  API bodies normalize line endings differently; raw substring matches miss."""
  return " ".join(text.split())


def record_failure(example_id: str, reason: str) -> None:
  FAILURES.parent.mkdir(parents=True, exist_ok=True)
  with FAILURES.open("a") as fh:
    fh.write(json.dumps({"example_id": example_id, "reason": reason}) + "\n")


def classify(source_row: dict, metadata: dict) -> str:
  if source_row.get("is_negative") or metadata.get("candidate_file") is None:
    return "negative"
  hint = source_row.get("category_hint") or source_row.get("comment_type") or "unknown"
  return hint if hint in CLASSES else "unknown"


def select_examples(
  classified: list[tuple[Path, str]], count: int, strategy: str
) -> list[tuple[Path, str]]:
  """Deterministic selection. Balanced: even per class, round-robin top-up.
  Per-class ordering is alphabetical and stable across counts, so raising
  --count only ever APPENDS new examples per class."""
  if strategy == "first":
    return classified[:count]
  by_class: dict[str, list[tuple[Path, str]]] = defaultdict(list)
  for entry in classified:
    by_class[entry[1]].append(entry)
  per_class = count // len(CLASSES)
  selected: list[tuple[Path, str]] = []
  for cls in CLASSES:
    selected.extend(by_class.get(cls, [])[:per_class])
  if len(selected) < count:
    leftovers = [entry for cls in CLASSES for entry in by_class.get(cls, [])[per_class:]]
    selected.extend(leftovers[: count - len(selected)])
  return selected


def split_for_repo(repository: str) -> str:
  """Family-atomic split by content hash of the repo name. Deterministic and
  independent of selection size — a repository's split never changes, which
  is what makes backfilling safe after annotation has started."""
  bucket = int(hashlib.sha256(repository.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
  if bucket < 0.2:
    return "development"
  if bucket < 0.5:
    return "validation"
  return "holdout"


async def api_get(client: httpx.AsyncClient, url: str) -> httpx.Response:
  """GET with rate-limit wait and transient-5xx backoff (6A pattern)."""
  resp = await client.get(url)
  for attempt in range(MAX_RETRIES):
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
      reset = int(resp.headers.get("x-ratelimit-reset", "0"))
      wait = max(reset - int(time.time()), 10)
      print(f"rate limited; sleeping {wait}s")
      await asyncio.sleep(wait)
    elif resp.status_code in (502, 503, 504):
      await asyncio.sleep(2**attempt)
    else:
      break
    resp = await client.get(url)
  return resp


async def resolve_gold_line(
  client: httpx.AsyncClient, metadata: dict, source_row: dict
) -> tuple[int | None, str | None]:
  """Anchor from the API's review-comment object. Returns (line, error)."""
  repo = metadata["repository"]
  pr = metadata["pr_number"]
  resp = await api_get(client, f"{GITHUB_API}/repos/{repo}/pulls/{pr}/comments")
  if resp.status_code != 200:
    return None, f"comments API {resp.status_code}"

  needle = normalize(source_row["reviewer_comment"])[:60]
  match = next(
    (c for c in resp.json() if needle in normalize(c.get("body") or "")),
    None,
  )
  if match is None:
    return None, "gold comment not found via API"
  if match.get("path") != source_row["file_path"]:
    return None, f"path mismatch: api={match.get('path')} pool={source_row['file_path']}"

  # `line` is the anchor in the current diff; `original_line` is where it
  # was when written (outdated comments). Our diff.patch is at head, but
  # the sanity check below rejects anchors that no longer exist there.
  line = match.get("line") or match.get("original_line")
  if line is None:
    return None, "comment has neither line nor original_line"

  legal = set(metadata.get("commentable_lines", [])) | set(metadata.get("right_side_lines", []))
  if legal and line not in legal:
    return None, f"api line {line} not in parser line sets (stale diff)"
  return line, None


def build_gold_comment(source_row: dict, metadata: dict, line: int) -> dict:
  category = CATEGORY_MAP.get(
    source_row.get("category_hint") or source_row.get("comment_type", ""),
    "maintainability",
  )
  return {
    "file_path": source_row["file_path"],
    "line": line,
    "category": category,
    "severity": "medium",  # TODO: annotate
    "issue_summary": source_row["reviewer_comment"][:500],
    "evidence_requirement": "TODO: what the agent must cite to get credit",
    "must_not_claim": [],
    "requires_repo_context": False,
    "requires_screenshot": False,
    "rationale": "TODO: why this gold issue is valid",
    "_reviewer_comment_full": source_row["reviewer_comment"],
    "_gold_comment_url": metadata.get("gold_comment_url"),
  }


def pool_example_to_golden(
  metadata: dict, source_row: dict, split: str, gold_comment: dict | None
) -> dict:
  is_negative = bool(source_row.get("is_negative"))
  return {
    "example_id": metadata["example_id"],
    "source": "github-codereview",
    "repository": metadata["repository"],
    "commit_sha": metadata["commit_sha"],
    "language": source_row.get("language", "Python"),
    "pr_metadata": {
      "title": metadata.get("title", ""),
      "description": metadata.get("description", ""),
      "url": metadata.get("pr_url", ""),
    },
    "changed_files": metadata["changed_files"],
    "diff_path": f"text/{split}/{metadata['example_id']}/diff.patch",
    "expected_outcome": "no_comment" if is_negative else "comment_expected",
    "gold_comments": [] if is_negative else [gold_comment],
    "context_files": [],
    "no_comment_rationale": ("Self-built verified negative (Phase 6A)" if is_negative else ""),
    "human_label_notes": "",
    "split": split,
    "snapshot_id": None,  # filled after indexing
  }


async def curate(pool_root: Path, out_root: Path, count: int, strategy: str) -> None:
  settings = get_settings()
  if not settings.github_dataset_token:
    raise SystemExit("GITHUB_DATASET_TOKEN is empty — fine-grained PAT, see .env")

  pool_dirs = sorted(p for p in pool_root.iterdir() if p.is_dir())
  if not pool_dirs:
    raise SystemExit(f"No pool example directories found in {pool_root}")

  # Scan pass: classify + load metadata for every pool dir (local reads only).
  classified: list[tuple[Path, str]] = []
  metadata_by_id: dict[str, dict] = {}
  source_row_by_id: dict[str, dict] = {}
  for pool_dir in pool_dirs:
    try:
      metadata = json.loads((pool_dir / "metadata.json").read_text())
      source_row = json.loads((pool_dir / "source_row.json").read_text())
    except (FileNotFoundError, json.JSONDecodeError) as exc:
      record_failure(pool_dir.name, f"scan: {type(exc).__name__}: {exc}")
      continue
    metadata_by_id[pool_dir.name] = metadata
    source_row_by_id[pool_dir.name] = source_row
    classified.append((pool_dir, classify(source_row, metadata)))

  selected = select_examples(classified, count, strategy)
  split_by_id = {
    pool_dir.name: split_for_repo(metadata_by_id[pool_dir.name]["repository"])
    for pool_dir, _cls in selected
  }

  class_counts = defaultdict(int)
  split_counts = defaultdict(int)
  for pool_dir, cls in selected:
    class_counts[cls] += 1
    split_counts[split_by_id[pool_dir.name]] += 1
  print(f"selected {len(selected)} of {len(classified)} pool examples ({strategy})")
  print(f"classes: {dict(sorted(class_counts.items()))}")
  print(f"splits:  {dict(sorted(split_counts.items()))}")

  session_maker = get_session_maker()
  github = GitHubClient(settings.github_dataset_token)
  llm = OpenRouterClient()

  needs_annotation: list[str] = []
  skipped = 0
  async with (
    httpx.AsyncClient(
      headers={
        "Authorization": f"Bearer {settings.github_dataset_token}",
        "X-GitHub-Api-Version": "2022-11-28",
      },
      timeout=30,
      follow_redirects=True,
    ) as api,
    session_maker() as session,
  ):
    for i, (pool_dir, _cls) in enumerate(selected):
      example_id = pool_dir.name
      try:
        metadata = metadata_by_id[example_id]
        source_row = source_row_by_id[example_id]
        split = split_by_id[example_id]
        is_negative = bool(source_row.get("is_negative"))

        # Resolve the anchor BEFORE any indexing spend (v5 ordering).
        gold_comment = None
        if not is_negative:
          line, error = await resolve_gold_line(api, metadata, source_row)
          if error is not None:
            record_failure(example_id, error)
            skipped += 1
            continue
          gold_comment = build_gold_comment(source_row, metadata, line)  # type: ignore[arg-type]

        golden = pool_example_to_golden(metadata, source_row, split, gold_comment)

        owner, repo = metadata["repository"].split("/")
        snapshot = await get_or_create_snapshot(
          session, owner=owner, repo=repo, sha=metadata["commit_sha"]
        )
        await index_snapshot(session, snapshot=snapshot, github=github, llm=llm)
        golden["snapshot_id"] = snapshot.id

        out_dir = out_root / split / example_id
        out_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(pool_dir / "diff.patch", out_dir / "diff.patch")
        (out_dir / "example.json").write_text(json.dumps(golden, indent=2))

        if gold_comment is not None:
          needs_annotation.append(example_id)

        print(f"[{i + 1}/{len(selected)}] {example_id} -> snapshot {snapshot.id} ({split})")
        await session.commit()
      except (json.JSONDecodeError, KeyError, httpx.HTTPError, ssl.SSLError) as exc:
        # ssl.SSLError: known-transient transport class (SSLV3_ALERT_BAD_RECORD_MAC
        # bursts on the embeddings endpoint); tenacity already retried it 3x.
        # Narrow tuple on purpose: unknown exceptions still kill the run loudly.
        record_failure(example_id, f"{type(exc).__name__}: {exc}")
        skipped += 1
        await session.rollback()

  await github.aclose()
  print(f"\nCurated {len(selected) - skipped}/{len(selected)} examples.")
  if skipped:
    print(f"{skipped} failures logged to {FAILURES}")
  if needs_annotation:
    print(f"{len(needs_annotation)} examples need annotation (TODO fields)")


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--pool", type=Path, default=POOL_ROOT)
  parser.add_argument("--out", type=Path, default=GOLDEN_TEXT_ROOT)
  parser.add_argument("--count", type=int, default=100)
  parser.add_argument("--strategy", choices=["balanced", "first"], default="balanced")
  args = parser.parse_args()
  asyncio.run(curate(args.pool, args.out, args.count, args.strategy))


if __name__ == "__main__":
  main()
