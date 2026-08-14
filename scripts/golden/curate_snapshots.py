"""Phase 7.2: curate repo snapshots for golden text examples.

For each unique (repository, commit_sha) in the requested splits:
get_or_create_snapshot -> index_snapshot (skips if already indexed)
-> write snapshot_id back into every example.json that references it.

Idempotent: re-running skips indexed snapshots and rewrites the same
snapshot_ids. Commits per snapshot BEFORE touching example.json — a
crash leaves at most one status="indexing" row and some unwritten
files; rerun to resume (cached snapshots redo the write-back).

--reindex: cached snapshots whose index is missing any of their PRs'
changed_files (coverage < COVERAGE_FLOOR, i.e. MAX_FILES truncation
ate the relevant code) are re-indexed with changed-file-priority
ordering. Cached snapshots at full coverage are left alone.

Failures (deleted repos, unreachable fork SHAs, embed errors) are
collected into curation_failures.json, not fatal. Examples without a
snapshot_id keep failing closed in the eval harness, as designed.

Coverage: per example we report the fraction of changed_files actually
indexed (curation_coverage.csv). Read baseline_b failures against
this — low coverage means retrieval never had the relevant code.

After any run that indexed something, bump DATASET_VERSION and rebuild
the manifest (annotation hashes change):
  uv run python scripts/golden/build_manifest.py

Usage:
  uv run python scripts/golden/curate_snapshots.py --limit 2 --reindex  # smoke
  uv run python scripts/golden/curate_snapshots.py --reindex            # dev+val
  uv run python scripts/golden/curate_snapshots.py --splits holdout     # later
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from sqlalchemy import delete, select

from app.core.config import settings
from app.db.models.repo_index import CodeChunk
from app.db.session import get_session_maker
from app.github.client import GitHubClient
from app.ingestion.indexer import (
  INDEXABLE_SUFFIXES,
  get_or_create_snapshot,
  index_snapshot,
  is_vendored_path,
)
from app.llm.openrouter_client import OpenRouterClient

GOLDEN_TEXT_ROOT = Path("data/golden/text")
DEFAULT_SPLITS = ("development", "validation")
COVERAGE_FLOOR = 1.0  # cached snapshots below this get re-indexed


def load_examples(splits: list[str]) -> list[tuple[Path, dict]]:
  """(path, example_dict) for every example.json in the given split dirs."""
  examples = []
  for split in splits:
    split_dir = GOLDEN_TEXT_ROOT / split
    if not split_dir.is_dir():
      raise SystemExit(f"no such split dir: {split_dir}")
    for path in sorted(split_dir.glob("*/example.json")):
      examples.append((path, json.loads(path.read_text())))
  return examples


def group_by_snapshot(
  examples: list[tuple[Path, dict]],
) -> dict[tuple[str, str], list[tuple[Path, dict]]]:
  """Dedupe to unique (repository, commit_sha) -> member examples."""
  groups: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
  for path, ex in examples:
    groups.setdefault((ex["repository"], ex["commit_sha"]), []).append((path, ex))
  return groups


def changed_file_coverage(example: dict, indexed_paths: set[str]) -> float | None:
  changed = [
    f
    for f in example.get("changed_files", [])
    if f.endswith(INDEXABLE_SUFFIXES) and not is_vendored_path(f)
  ]
  if not changed:
    return None
  hits = sum(1 for f in changed if f in indexed_paths)
  return hits / len(changed)


def build_github_client() -> GitHubClient:
  if not settings.github_dataset_token:
    raise SystemExit("GITHUB_DATASET_TOKEN is empty — fine-grained PAT, see .env")
  return GitHubClient(settings.github_dataset_token)


async def curate(splits: list[str], out_dir: Path, limit: int | None, reindex: bool) -> None:
  examples = load_examples(splits)
  groups = group_by_snapshot(examples)
  print(f"{len(examples)} examples -> {len(groups)} unique snapshots")

  github = build_github_client()
  llm = OpenRouterClient()
  session_maker = get_session_maker()

  failures: list[dict] = []
  coverage_rows: list[tuple[str, int, float | None, int]] = []
  indexed = cached = 0

  items = sorted(groups.items())
  if limit:
    items = items[:limit]

  try:
    async with session_maker() as session:
      for i, ((repository, sha), members) in enumerate(items, 1):
        owner, repo = repository.split("/", 1)
        label = f"[{i}/{len(items)}] {repository}@{sha[:8]} ({len(members)} ex)"
        try:
          snapshot = await get_or_create_snapshot(session, owner=owner, repo=repo, sha=sha)
          was_indexed = snapshot.status == "indexed"

          if was_indexed and reindex:
            pre_paths = set(
              (
                await session.scalars(
                  select(CodeChunk.file_path).where(CodeChunk.snapshot_id == snapshot.id).distinct()
                )
              ).all()
            )
            worst = min(
              (c for _, ex in members if (c := changed_file_coverage(ex, pre_paths)) is not None),
              default=None,
            )
            if worst is not None and worst < COVERAGE_FLOOR:
              snapshot.status = "indexing"
              was_indexed = False

          if not was_indexed:
            # Fresh, crashed-partial, or reindex-forced: clear existing
            # chunks so re-indexing never duplicates, then index with
            # the PRs' changed files prioritized under the MAX_FILES cap.
            await session.execute(delete(CodeChunk).where(CodeChunk.snapshot_id == snapshot.id))
            priority = {f for _, ex in members for f in ex.get("changed_files", [])}
            await index_snapshot(
              session, snapshot=snapshot, github=github, llm=llm, priority_files=priority
            )

          indexed_paths = set(
            (
              await session.scalars(
                select(CodeChunk.file_path).where(CodeChunk.snapshot_id == snapshot.id).distinct()
              )
            ).all()
          )

          await session.commit()

          # Write-back AFTER commit: if we die here, the rerun finds the
          # snapshot cached and redoes the write-back. Never the reverse.
          for path, ex in members:
            ex["snapshot_id"] = snapshot.id
            path.write_text(json.dumps(ex, indent=2, ensure_ascii=False) + "\n")
            coverage_rows.append(
              (
                ex["example_id"],
                snapshot.id,
                changed_file_coverage(ex, indexed_paths),
                len(ex.get("changed_files", [])),
              )
            )

          if was_indexed:
            cached += 1
          else:
            indexed += 1
          state = "cached" if was_indexed else f"indexed ({snapshot.chunk_count} chunks)"
          print(f"{label} -> snapshot {snapshot.id} {state}")
        except Exception as exc:
          await session.rollback()
          failures.append(
            {
              "repository": repository,
              "commit_sha": sha,
              "examples": [ex["example_id"] for _, ex in members],
              "error": f"{type(exc).__name__}: {exc}",
            }
          )
          print(f"{label} -> FAILED ({type(exc).__name__}: {exc})")
  finally:
    await github.aclose()

  out_dir.mkdir(parents=True, exist_ok=True)
  (out_dir / "curation_failures.json").write_text(json.dumps(failures, indent=2) + "\n")
  with (out_dir / "curation_coverage.csv").open("w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow(["example_id", "snapshot_id", "changed_file_coverage", "changed_files"])
    writer.writerows(coverage_rows)

  print(f"\nindexed {indexed}, cached {cached}, failed {len(failures)}")
  coverages = [r[2] for r in coverage_rows if r[2] is not None]
  if coverages:
    print(f"mean changed-file coverage: {sum(coverages) / len(coverages):.2f}")
    low = [r for r in coverage_rows if r[2] is not None and r[2] < 1.0]
    if low:
      print(f"{len(low)} examples with incomplete coverage — see curation_coverage.csv")
  if failures:
    print(f"failures -> {out_dir / 'curation_failures.json'}")
  if indexed:
    print("next: bump DATASET_VERSION, then uv run python scripts/golden/build_manifest.py")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--splits",
    default=",".join(DEFAULT_SPLITS),
    help="comma-separated split dirs (default: development,validation; holdout is opt-in)",
  )
  parser.add_argument("--out", default="data/processed/curation")
  parser.add_argument(
    "--limit", type=int, default=None, help="curate only the first N snapshots (smoke test)"
  )
  parser.add_argument(
    "--reindex",
    action="store_true",
    help="re-index cached snapshots below COVERAGE_FLOOR",
  )
  args = parser.parse_args()
  splits = [s.strip() for s in args.splits.split(",") if s.strip()]
  asyncio.run(curate(splits, Path(args.out), args.limit, args.reindex))


if __name__ == "__main__":
  main()
