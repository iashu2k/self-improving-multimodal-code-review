# scripts/golden/fetch_pr_context.py
"""Rebuild PR-level examples from harvested candidates.

HF rows are triplets (chunks), not PRs. For each candidate, fetch from GitHub:
  1. full PR diff (diff media type)      -> diff.patch
  2. PR metadata (head SHA, title, body) -> metadata.json
  3. review comments                     -> negative verification + gold URL

Line anchors are deliberately NOT taken from the dataset's comment_line
(chunk-relative) or the API's diff-position fields. metadata.json carries the
commentable/right-side line sets computed by the PRODUCTION diff parser, so
annotation picks anchors from the same legal set the validator enforces —
labels and the gate can never disagree.

Idempotent: examples with an existing metadata.json are skipped; re-run
after a crash to resume. Failures append to enrichment_failures.jsonl.
"""

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx

from app.core.config import get_settings

# adjust if module is diffparser.py
from app.github.diff_parser import parse_unified_diff

API = "https://api.github.com"
CANDIDATES = Path("data/golden_prs/candidates/candidates.jsonl")
POOL = Path("data/golden_prs/pool")
FAILURES = Path("data/golden_prs/candidates/enrichment_failures.jsonl")

MAX_CHANGED_FILES = 30  # triage-size philosophy: golden PRs must be reviewable
CONCURRENCY = 4  # ~510 x 3 requests << 5000/hr; no heroics needed
MAX_RETRIES = 3


def normalize(text: str) -> str:
    """Collapse \\r\\n, tabs, and space runs — HF comments and API bodies
    normalize line endings differently, and a raw substring match silently misses."""
    return " ".join(text.split())


class SkipExample(Exception):
    """Candidate cannot become an example; the reason is recorded for audit."""


def record_failure(repo: str, pr: int, reason: str) -> None:
    FAILURES.parent.mkdir(parents=True, exist_ok=True)
    with FAILURES.open("a") as fh:
        fh.write(json.dumps({"repo": repo, "pr": pr, "reason": reason}) + "\n")


async def get(client: httpx.AsyncClient, url: str, accept: str) -> httpx.Response:
    """GET with rate-limit wait and transient-5xx backoff. Permanent errors
    (404/410/451) return immediately — the caller decides what they mean."""
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


async def fetch_one(client: httpx.AsyncClient, cand: dict, counters: Counter) -> str | None:
    repo, pr = cand["repo_name"], cand["pr_number"]
    ex_id = f"{repo.lower().replace('/', '__')}__pr_{pr:06d}"
    ex_dir = POOL / ex_id

    if (ex_dir / "metadata.json").exists():
        counters["skip_already_enriched"] += 1
        return ex_id

    try:
        # 1. full PR diff (real API payload — never hand-written fixtures)
        diff_resp = await get(
            client, f"{API}/repos/{repo}/pulls/{pr}", "application/vnd.github.v3.diff"
        )
        if diff_resp.status_code in (404, 410, 451):
            raise SkipExample(f"pr unavailable ({diff_resp.status_code})")
        diff_resp.raise_for_status()
        diff_text = diff_resp.text
        if not diff_text.strip():
            raise SkipExample("empty diff")

        # 2. PR metadata
        meta_resp = await get(
            client, f"{API}/repos/{repo}/pulls/{pr}", "application/vnd.github+json"
        )
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        # 3. review comments
        comments_resp = await get(
            client, f"{API}/repos/{repo}/pulls/{pr}/comments", "application/vnd.github+json"
        )
        comments_resp.raise_for_status()
        review_comments = comments_resp.json()

        # Negatives must be TRULY comment-free, not just "no comment on this chunk"
        if cand["is_negative"] and review_comments:
            raise SkipExample(f"negative has {len(review_comments)} review comments")

        # Parse with the PRODUCTION parser. Deleted files parse as path ""
        # (their "+++" target is /dev/null) — filter them out of changed.
        parsed_files = parse_unified_diff(diff_text)
        changed = [f.path for f in parsed_files if f.path]
        if not changed:
            raise SkipExample("no files parsed from diff")
        if len(changed) > MAX_CHANGED_FILES:
            raise SkipExample(f"too many files ({len(changed)})")

        candidate = next((f for f in parsed_files if f.path == cand["file_path"]), None)
        if not cand["is_negative"]:
            if candidate is None:
                raise SkipExample(f"candidate file {cand['file_path']} not in diff")
            if not candidate.commentable_lines:
                raise SkipExample(f"candidate file {cand['file_path']} has no added lines")

        # Gold comment URL: best-effort convenience for the annotator.
        # Never a correctness input — anchors come from the diff parser.
        gold_url = None
        if not cand["is_negative"]:
            needle = normalize(cand["reviewer_comment"])[:60]
            gold_url = next(
                (
                    c["html_url"]
                    for c in review_comments
                    if needle in normalize(c.get("body") or "")
                ),
                None,
            )
            if gold_url is None:
                counters["warn_gold_url_unresolved"] += 1

        ex_dir.mkdir(parents=True, exist_ok=True)
        (ex_dir / "diff.patch").write_text(diff_text)
        (ex_dir / "source_row.json").write_text(json.dumps(cand, indent=2))
        (ex_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "example_id": ex_id,
                    "repository": repo,
                    "pr_number": pr,
                    "commit_sha": meta["head"]["sha"],
                    "base_sha": meta["base"]["sha"],
                    "title": meta["title"],
                    "description": meta.get("body") or "",
                    "pr_url": meta["html_url"],
                    "changed_files": changed,
                    "candidate_file": cand["file_path"] if not cand["is_negative"] else None,
                    # Legal anchor sets from the production parser: added lines are the
                    # preferred anchor; right-side context lines are the legal fallback
                    # for deletion findings (Phase 3B anchoring policy).
                    "commentable_lines": sorted(candidate.commentable_lines) if candidate else [],
                    "right_side_lines": sorted(candidate.right_side_lines) if candidate else [],
                    "category_hint": cand["category_hint"],
                    "gold_comment_url": gold_url,
                },
                indent=2,
            )
        )
        counters["enriched"] += 1
        return ex_id

    except SkipExample as exc:
        counters["skip_candidate"] += 1
        record_failure(repo, pr, str(exc))
        return None
    except httpx.HTTPError as exc:
        counters["error_http"] += 1
        record_failure(repo, pr, f"http: {exc}")
        return None


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=None, help="smoke-test the first N candidates only"
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.github_dataset_token:
        raise SystemExit("GITHUB_DATASET_TOKEN is empty — fine-grained PAT, see .env")

    cands = [json.loads(line) for line in CANDIDATES.read_text().splitlines()]
    if args.limit:
        cands = cands[: args.limit]

    counters: Counter = Counter()
    async with httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {settings.github_dataset_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=30,
        follow_redirects=True,  # renamed repos 301 — recover them for free
    ) as client:
        sem = asyncio.Semaphore(CONCURRENCY)

        async def guarded(cand: dict):
            async with sem:
                return await fetch_one(client, cand, counters)

        results = await asyncio.gather(*(guarded(c) for c in cands))

    ok = [r for r in results if r]
    print(f"enriched {len(ok)}/{len(cands)} candidates -> {POOL}/")
    print("counters:", dict(counters.most_common()))
    if FAILURES.exists():
        print(f"skip/error reasons logged to {FAILURES}")


if __name__ == "__main__":
    asyncio.run(main())
