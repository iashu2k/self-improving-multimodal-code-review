"""Re-curate dead-at-head positives at the revision the reviewer actually saw.

Usage:
  uv run python scripts/golden/recurate_at_review_revision.py --limit 3   # smoke
  uv run python scripts/golden/recurate_at_review_revision.py             # full

Why this exists: ~75% of pool positives are dead at HEAD — merged PRs in
review-active repos apply most review feedback before merge, so the head
diff is the post-review state (measured by the pre_annotate presence gate:
61 exclusions + ~20 stale-anchor curation failures out of 112 curated).
At the comment's original_commit_id the issue exists BY CONSTRUCTION — the
human wrote the comment on that revision.

Per candidate example:
  diff     = GET /repos/{repo}/compare/{base_sha}...{original_commit_id}
             with the v3.diff media type (a real unified diff — the
             production parser consumes it directly, no synthesis)
  anchor   = comment.original_line, verified against right-side line sets
             computed by the PRODUCTION parser on THAT diff (decision 17)
  snapshot = indexed at original_commit_id (keys are (repo, sha) — no
             collision with the head-revision snapshots)
  split    = preserved from the existing example.json when present, else
             the same sha256(repo) bucket as curation

Candidates: everything in data/golden/text/_excluded/ plus curation
failures of class "stale diff" (anchor gone at head). Live-at-head
positives and negatives are untouched. Successful re-curations are marked
"diff_revision": "review_comment_time" (head-revision examples implicitly
differ — DATASET_CARD documents the mix) and the _excluded copy is removed.
GoldComment fields stay TODO-marked; run pre_annotate afterwards.

Idempotent: an example already at its review revision is skipped (the
split-dir lookup checks the three split dirs EXPLICITLY — a glob of
*/<id>/example.json would also match _excluded/ and re-curate forever).
Failures append to curation_failures.jsonl with a "recurate:" prefix.
Requires GITHUB_DATASET_TOKEN.
"""

import argparse
import asyncio
import hashlib
import json
import shutil
import time
import warnings
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.db.session import get_session_maker
from app.github.client import GitHubClient
from app.github.diff_parser import parse_unified_diff
from app.ingestion.indexer import get_or_create_snapshot, index_snapshot
from app.llm.openrouter_client import OpenRouterClient

# ast.parse inside the chunker parses third-party repo files.
warnings.filterwarnings("ignore", category=SyntaxWarning)

GITHUB_API = "https://api.github.com"
POOL_ROOT = Path("data/golden_prs/pool")
GOLDEN_TEXT_ROOT = Path("data/golden/text")
EXCLUDED_ROOT = GOLDEN_TEXT_ROOT / "_excluded"
FAILURES = GOLDEN_TEXT_ROOT / "curation_failures.jsonl"
SPLIT_DIRS = ("development", "validation", "holdout")

MAX_CHANGED_FILES = 30  # same triage-size philosophy as 6A
MAX_RETRIES = 3

CATEGORY_MAP = {
  "bug": "bug_risk",
  "security": "security",
  "refactor": "maintainability",
  "performance": "performance",
}


def normalize(text: str) -> str:
  """6A normalization: HF comment bodies and API bodies normalize line
  endings differently; raw substring matches miss."""
  return " ".join(text.split())


def record_failure(example_id: str, reason: str) -> None:
  FAILURES.parent.mkdir(parents=True, exist_ok=True)
  with FAILURES.open("a") as fh:
    fh.write(json.dumps({"example_id": example_id, "reason": reason}) + "\n")


def split_for_repo(repository: str) -> str:
  """Same hash bucket as curate_text_examples.py — a repo's split never
  changes regardless of which script assigns it."""
  bucket = int(hashlib.sha256(repository.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
  if bucket < 0.2:
    return "development"
  if bucket < 0.5:
    return "validation"
  return "holdout"


def collect_candidates() -> list[str]:
  """Excluded examples + stale-anchor curation failures, deduped."""
  candidates: set[str] = set()
  if EXCLUDED_ROOT.is_dir():
    for d in EXCLUDED_ROOT.iterdir():
      if d.is_dir():
        candidates.add(d.name)
  if FAILURES.exists():
    for line in FAILURES.read_text().splitlines():
      try:
        entry = json.loads(line)
      except json.JSONDecodeError:
        continue
      if "stale diff" in entry.get("reason", ""):
        candidates.add(entry["example_id"])
  return sorted(candidates)


async def api_get(
  client: httpx.AsyncClient, url: str, accept: str = "application/vnd.github+json"
) -> httpx.Response:
  """GET with rate-limit wait and transient-5xx backoff (6A pattern)."""
  resp = await client.get(url, headers={"Accept": accept})
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
    resp = await client.get(url, headers={"Accept": accept})
  return resp


async def find_review_comment(
  client: httpx.AsyncClient, metadata: dict, source_row: dict
) -> tuple[dict | None, str | None]:
  """The matched API comment object. Same needle logic as curation."""
  repo, pr = metadata["repository"], metadata["pr_number"]
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
  return match, None


async def fetch_revision_diff(
  client: httpx.AsyncClient, metadata: dict, original_commit_id: str
) -> tuple[str | None, str | None]:
  """Unified diff of the PR at the comment's revision: merge-base of the
  (current) base and original_commit_id ... original_commit_id. The base
  branch may have advanced since; the merge-base computation makes this the
  fork-point diff — what the reviewer saw, absent rebases."""
  repo = metadata["repository"]
  base = metadata["base_sha"]
  resp = await api_get(
    client,
    f"{GITHUB_API}/repos/{repo}/compare/{base}...{original_commit_id}",
    accept="application/vnd.github.v3.diff",
  )
  if resp.status_code in (404, 410):
    return None, f"revision commit unreachable ({resp.status_code}) — force-push?"
  if resp.status_code != 200:
    return None, f"compare API {resp.status_code}"
  if not resp.text.strip():
    return None, "empty compare diff"
  return resp.text, None


def verify_anchor(diff_text: str, file_path: str, line: int) -> tuple[list, str | None]:
  """Parse the revision diff with the production parser; verify the anchor
  is a legal RIGHT-side line in the candidate file. Returns (files, error)."""
  files = [f for f in parse_unified_diff(diff_text) if f.path]
  if not files:
    return [], "no files parsed from revision diff"
  if len(files) > MAX_CHANGED_FILES:
    return [], f"revision diff too large ({len(files)} files)"
  for f in files:
    if f.path == file_path:
      if line in f.right_side_lines:
        return files, None
      return [], f"original_line {line} not legal in revision diff"
  return [], f"candidate file {file_path} not in revision diff"


def build_golden(
  metadata: dict,
  source_row: dict,
  split: str,
  original_commit_id: str,
  original_line: int,
  changed_files: list[str],
) -> dict:
  category = CATEGORY_MAP.get(
    source_row.get("category_hint") or source_row.get("comment_type", ""),
    "maintainability",
  )
  return {
    "example_id": metadata["example_id"],
    "source": "github-codereview",
    "repository": metadata["repository"],
    "commit_sha": original_commit_id,
    "diff_revision": "review_comment_time",
    "language": source_row.get("language", "Python"),
    "pr_metadata": {
      "title": metadata.get("title", ""),
      "description": metadata.get("description", ""),
      "url": metadata.get("pr_url", ""),
    },
    "changed_files": changed_files,
    "diff_path": f"text/{split}/{metadata['example_id']}/diff.patch",
    "expected_outcome": "comment_expected",
    "gold_comments": [
      {
        "file_path": source_row["file_path"],
        "line": original_line,
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
    ],
    "context_files": [],
    "no_comment_rationale": "",
    "human_label_notes": "",
    "split": split,
    "snapshot_id": None,  # filled after indexing
  }


def find_existing(example_id: str) -> tuple[Path | None, Path]:
  """(example.json in a real split dir, excluded example.json path).

  The split lookup is explicit — a glob of */<id>/example.json would also
  match _excluded/ and make the idempotency check below useless."""
  for split in SPLIT_DIRS:
    candidate = GOLDEN_TEXT_ROOT / split / example_id / "example.json"
    if candidate.exists():
      return candidate, EXCLUDED_ROOT / example_id / "example.json"
  return None, EXCLUDED_ROOT / example_id / "example.json"


async def recurate(count: int | None) -> None:
  settings = get_settings()
  if not settings.github_dataset_token:
    raise SystemExit("GITHUB_DATASET_TOKEN is empty — fine-grained PAT, see .env")

  candidates = collect_candidates()
  if count:
    candidates = candidates[:count]
  if not candidates:
    raise SystemExit("No candidates: _excluded/ empty and no stale-diff failures")
  print(f"re-curating {len(candidates)} candidates at review revision")

  session_maker = get_session_maker()
  github = GitHubClient(settings.github_dataset_token)
  llm = OpenRouterClient()

  revived, skipped = 0, 0
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
    for i, example_id in enumerate(candidates):
      try:
        pool_dir = POOL_ROOT / example_id
        metadata = json.loads((pool_dir / "metadata.json").read_text())
        source_row = json.loads((pool_dir / "source_row.json").read_text())

        split_path, excluded_path = find_existing(example_id)
        if split_path is not None:
          existing = json.loads(split_path.read_text())
          if existing.get("diff_revision") == "review_comment_time":
            skipped += 1  # already re-curated
            continue
          split = existing["split"]
        elif excluded_path.exists():
          split = json.loads(excluded_path.read_text())["split"]
        else:
          split = split_for_repo(metadata["repository"])

        comment, error = await find_review_comment(api, metadata, source_row)
        if error:
          record_failure(example_id, f"recurate: {error}")
          skipped += 1
          continue
        original_commit_id = comment.get("original_commit_id")
        original_line = comment.get("original_line") or comment.get("line")
        if not original_commit_id or original_line is None:
          record_failure(example_id, "recurate: comment lacks original_commit_id/line")
          skipped += 1
          continue

        diff_text, error = await fetch_revision_diff(api, metadata, original_commit_id)
        if error:
          record_failure(example_id, f"recurate: {error}")
          skipped += 1
          continue

        files, error = verify_anchor(diff_text, source_row["file_path"], original_line)
        if error:
          record_failure(example_id, f"recurate: {error}")
          skipped += 1
          continue

        owner, repo = metadata["repository"].split("/")
        snapshot = await get_or_create_snapshot(
          session, owner=owner, repo=repo, sha=original_commit_id
        )
        await index_snapshot(session, snapshot=snapshot, github=github, llm=llm)

        golden = build_golden(
          metadata,
          source_row,
          split,
          original_commit_id,
          original_line,
          [f.path for f in files],
        )
        golden["snapshot_id"] = snapshot.id

        out_dir = GOLDEN_TEXT_ROOT / split / example_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "diff.patch").write_text(diff_text)
        (out_dir / "example.json").write_text(json.dumps(golden, indent=2) + "\n")
        if excluded_path.exists():
          shutil.rmtree(excluded_path.parent)

        revived += 1
        print(
          f"[{i + 1}/{len(candidates)}] {example_id} revived @ "
          f"{original_commit_id[:8]} -> {split} (snapshot {snapshot.id})"
        )
        await session.commit()
      except (json.JSONDecodeError, KeyError, httpx.HTTPError) as exc:
        record_failure(example_id, f"recurate: {type(exc).__name__}: {exc}")
        skipped += 1
        await session.rollback()

  await github.aclose()
  print(f"\nRevived {revived}/{len(candidates)} at review revision, {skipped} skipped.")
  print("Next: uv run python scripts/golden/pre_annotate.py  # drafts the revived TODOs")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--limit", type=int, default=None, help="smoke-test N candidates")
  args = parser.parse_args()
  asyncio.run(recurate(args.limit))


if __name__ == "__main__":
  main()
