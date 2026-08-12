# scripts/golden/fetch_negatives.py
"""Build PR-level NO_COMMENT golden negatives.

Why this exists: the HF dataset's is_negative rows are chunk-level ("this
chunk got no comment"). API probe: 97/100 candidate negative PRs had HUMAN
review feedback elsewhere (10-sample: 36 human vs 1 bot comments). A golden
negative's contract is stricter: no human found anything worth saying on
the WHOLE PR, and the code still merged.

Source: repos already in the pool — proven active review cultures, real
Python codebases. A merged-with-zero-human-feedback PR from a repo where
maintainers DO review is a meaningful negative, not a cherry-picked one.

Contract per candidate:
  - merged (merged_at non-null) — code passed review
  - human-authored, not draft
  - small (files/additions caps) and Python-touching with >=1 added line
  - ZERO human inline review comments (bots excluded; ghost users = human)
  - no human CHANGES_REQUESTED or COMMENTED-with-body reviews
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx

from app.core.config import get_settings
from app.github.diff_parser import parse_unified_diff, reviewable_files

API = "https://api.github.com"
POOL = Path("data/golden_prs/pool")
FAILURES = Path("data/golden_prs/candidates/negative_failures.jsonl")

MIN_ADDITIONS = 3
MAX_ADDITIONS = 200
MAX_FILES = 5
SCAN_LIMIT = 40  # closed PRs examined per repo
CONCURRENCY = 4
MAX_RETRIES = 3


def human_only(items: list[dict]) -> list[dict]:
  """Strip bot-authored items; null user (deleted account) counts as human."""
  return [c for c in items if (c.get("user") or {}).get("type", "User") != "Bot"]


def disqualifying_reviews(reviews: list[dict]) -> list[dict]:
  bad = []
  for r in human_only(reviews):
    state = r.get("state", "")
    if state == "CHANGES_REQUESTED" or state == "COMMENTED" and (r.get("body") or "").strip():
      bad.append(r)
  return bad


def record_failure(repo: str, pr: int, reason: str) -> None:
  FAILURES.parent.mkdir(parents=True, exist_ok=True)
  with FAILURES.open("a") as fh:
    fh.write(json.dumps({"repo": repo, "pr": pr, "reason": reason}) + "\n")


async def get(client: httpx.AsyncClient, url: str, accept: str) -> httpx.Response:
  headers = {"Accept": accept}
  resp = await client.get(url, headers=headers)
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
    resp = await client.get(url, headers=headers)
  return resp


async def scan_repo(
  client: httpx.AsyncClient, repo: str, per_repo: int, counters: Counter
) -> list[str]:
  kept: list[str] = []
  resp = await get(
    client,
    f"{API}/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page={SCAN_LIMIT}",
    "application/vnd.github+json",
  )
  if resp.status_code != 200:
    counters["repo_list_failed"] += 1
    record_failure(repo, 0, f"list pulls: {resp.status_code}")
    return kept

  for pr in resp.json():
    if len(kept) >= per_repo:
      break
    n = pr["number"]
    if not pr.get("merged_at"):
      counters["skip_not_merged"] += 1
      continue
    if pr.get("draft") or (pr.get("user") or {}).get("type") == "Bot":
      counters["skip_draft_or_bot_author"] += 1
      continue

    ex_id = f"{repo.lower().replace('/', '__')}__pr_{n:06d}"
    ex_dir = POOL / ex_id
    if ex_dir.exists():
      counters["skip_in_pool"] += 1
      continue

    detail = await get(client, f"{API}/repos/{repo}/pulls/{n}", "application/vnd.github+json")
    if detail.status_code != 200:
      counters["skip_detail"] += 1
      continue
    d = detail.json()
    if not (MIN_ADDITIONS <= d["additions"] <= MAX_ADDITIONS) or d["changed_files"] > MAX_FILES:
      counters["skip_size"] += 1
      continue

    comments_resp = await get(
      client, f"{API}/repos/{repo}/pulls/{n}/comments", "application/vnd.github+json"
    )
    if comments_resp.status_code != 200:
      continue
    if human_only(comments_resp.json()):
      counters["skip_human_comments"] += 1
      continue

    reviews_resp = await get(
      client, f"{API}/repos/{repo}/pulls/{n}/reviews", "application/vnd.github+json"
    )
    if reviews_resp.status_code != 200:
      continue
    if disqualifying_reviews(reviews_resp.json()):
      counters["skip_review_feedback"] += 1
      continue

    diff_resp = await get(client, f"{API}/repos/{repo}/pulls/{n}", "application/vnd.github.v3.diff")
    if diff_resp.status_code != 200 or not diff_resp.text.strip():
      counters["skip_diff"] += 1
      continue

    parsed = parse_unified_diff(diff_resp.text)
    py_files = [f for f in reviewable_files(parsed) if f.path.endswith(".py")]
    if not any(f.commentable_lines for f in py_files):
      counters["skip_no_python_changes"] += 1
      continue

    ex_dir.mkdir(parents=True, exist_ok=True)
    (ex_dir / "diff.patch").write_text(diff_resp.text)
    (ex_dir / "source_row.json").write_text(
      json.dumps(
        {
          "repo_name": repo,
          "pr_number": n,
          "is_negative": True,
          "source": "self-built negative (merged, zero human review feedback)",
        },
        indent=2,
      )
    )
    (ex_dir / "metadata.json").write_text(
      json.dumps(
        {
          "example_id": ex_id,
          "repository": repo,
          "pr_number": n,
          "commit_sha": d["head"]["sha"],
          "base_sha": d["base"]["sha"],
          "title": d["title"],
          "description": d.get("body") or "",
          "pr_url": d["html_url"],
          "changed_files": [f.path for f in parsed if f.path],
          "candidate_file": None,
          "commentable_lines": [],
          "right_side_lines": [],
          "category_hint": "none",
          "gold_comment_url": None,
        },
        indent=2,
      )
    )
    kept.append(ex_id)
    counters["kept"] += 1
  return kept


async def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--target",
    type=int,
    default=70,
    help="negatives to collect (need ~40+ to survive annotation)",
  )
  PER_REPO = 2
  parser.add_argument("--per-repo", type=int, default=PER_REPO)
  parser.add_argument("--max-repos", type=int, default=60)
  args = parser.parse_args()

  settings = get_settings()
  if not settings.github_dataset_token:
    raise SystemExit("GITHUB_DATASET_TOKEN is empty — fine-grained PAT, see .env")

  repo_counts = Counter(
    json.loads(m.read_text())["repository"] for m in POOL.glob("*/metadata.json")
  )
  repos = [r for r, _ in repo_counts.most_common()][: args.max_repos]
  print(f"scanning {len(repos)} pool repos for <= {args.target} negatives")

  counters: Counter = Counter()
  async with httpx.AsyncClient(
    headers={
      "Authorization": f"Bearer {settings.github_dataset_token}",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    timeout=30,
    follow_redirects=True,
  ) as client:
    sem = asyncio.Semaphore(CONCURRENCY)

    async def guarded(repo: str):
      async with sem:
        if counters["kept"] >= args.target:
          return []
        return await scan_repo(client, repo, args.per_repo, counters)

    await asyncio.gather(*(guarded(r) for r in repos))

  print(f"kept {counters['kept']} negatives (target {args.target})")
  print("funnel:", dict(counters.most_common()))


if __name__ == "__main__":
  asyncio.run(main())
