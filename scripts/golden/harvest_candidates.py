# scripts/golden/harvest_candidates.py
"""Harvest candidate review examples from ronantakizawa/github-codereview.

The HF rows are TRIPLETS (before/after/comment), not PRs. This script only
builds the candidate pool; fetch_pr_context.py rebuilds PR-level examples.

Modes:
  default:              fresh pool, 350 positives + 100 negatives (writes file;
                        refuses to overwrite an existing pool without --force)
  --topup-type TYPE:    append more candidates of one scarce comment_type with
                        relaxed filters (annotation is the real quality gate)
"""

import argparse
import json
from collections import Counter
from pathlib import Path

from datasets import load_dataset

OUT = Path("data/golden_prs/candidates/candidates.jsonl")

POSITIVE_TYPES = {"bug", "security", "performance", "refactor"}
MAX_SNIPPET_LINES = 400  # proxy for "exclude enormous diffs" pre-fetch

# default mode
MAX_PER_REPO = 3  # diversity across repos
POS_TARGET = 350
NEG_TARGET = 100

# top-up mode (scarce categories; annotation will cull hard, so loosen up)
TOPUP_MAX_PER_REPO = 8
TOPUP_MIN_QUALITY = 0.5
TOPUP_MIN_COMMENT_CHARS = 60

VAGUE_MARKERS = ("lol", "+1", "lgtm", "nice", "thanks", "what do you think")


def is_vague(comment: str) -> bool:
  c = comment.strip().lower()
  return len(c) < 40 or any(m in c for m in VAGUE_MARKERS)


def parse_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
    "--topup-type",
    default=None,
    choices=sorted(POSITIVE_TYPES),
    help="append-mode top-up for one scarce comment_type",
  )
  parser.add_argument(
    "--topup-target", type=int, default=60, help="how many top-up candidates to add"
  )
  parser.add_argument(
    "--force",
    action="store_true",
    help="default mode: overwrite an existing pool (erases top-ups)",
  )
  parser.add_argument("--out", default=str(OUT))
  return parser.parse_args()


def main() -> None:
  args = parse_args()
  out_path = Path(args.out)
  topup = args.topup_type is not None

  if not topup and out_path.exists() and not args.force:
    raise SystemExit(
      f"{out_path} already exists — use --topup-type to append, "
      "or --force to overwrite (this erases any top-ups)."
    )

  ds = load_dataset("ronantakizawa/github-codereview", split="train")
  print(ds)

  seen_prs: set[tuple[str, int]] = set()
  per_repo: dict[str, int] = {}

  # Top-up appends to the existing pool: seed dedup so we never duplicate.
  if topup and out_path.exists():
    for line in out_path.read_text().splitlines():
      row = json.loads(line)
      seen_prs.add((row["repo_name"], row["pr_number"]))
      per_repo[row["repo_name"]] = per_repo.get(row["repo_name"], 0) + 1
    print(f"seeded dedup from {len(seen_prs)} existing candidates")

  funnel: Counter[str] = Counter()
  kept_pos = kept_neg = kept_topup = 0

  out_path.parent.mkdir(parents=True, exist_ok=True)
  with out_path.open("a" if topup else "w") as fh:
    for row in ds:
      funnel["rows"] += 1
      if topup and kept_topup >= args.topup_target:
        break
      if not topup and kept_pos + kept_neg >= POS_TARGET + NEG_TARGET:
        break

      lang = str(row["language"] or row["repo_language"] or "").strip().lower()
      if lang != "python":
        funnel["drop_language"] += 1
        continue

      key = (row["repo_name"], row["pr_number"])
      if key in seen_prs:
        funnel["drop_dup_pr"] += 1
        continue

      repo_cap = TOPUP_MAX_PER_REPO if topup else MAX_PER_REPO
      if per_repo.get(row["repo_name"], 0) >= repo_cap:
        funnel["drop_repo_cap"] += 1
        continue
      if row["before_lines"] + row["after_lines"] > MAX_SNIPPET_LINES:
        funnel["drop_too_big"] += 1
        continue

      if topup:
        if row["is_negative"] or row["comment_type"] != args.topup_type:
          funnel["drop_topup_type"] += 1
          continue
        score = row["quality_score"]
        if (
          score is None
          or score < TOPUP_MIN_QUALITY
          or len(row["reviewer_comment"]) < TOPUP_MIN_COMMENT_CHARS
        ):
          funnel["drop_quality"] += 1
          continue
        category_hint = row["comment_type"]
        kept_topup += 1
      elif row["is_negative"]:
        if kept_neg >= NEG_TARGET:
          funnel["drop_neg_quota"] += 1
          continue
        category_hint = "none"
        kept_neg += 1
      else:
        if kept_pos >= POS_TARGET:
          funnel["drop_pos_quota"] += 1
          continue
        if row["comment_type"] not in POSITIVE_TYPES:
          funnel["drop_type"] += 1
          continue
        score = row["quality_score"]
        if score is None or score < 0.7 or is_vague(row["reviewer_comment"]):
          funnel["drop_quality"] += 1
          continue
        category_hint = row["comment_type"]
        kept_pos += 1

      seen_prs.add(key)
      per_repo[row["repo_name"]] = per_repo.get(row["repo_name"], 0) + 1
      fh.write(json.dumps({**row, "category_hint": category_hint}) + "\n")
      funnel["kept"] += 1

  if topup:
    print(
      f"top-up: added {kept_topup} '{args.topup_type}' candidates (requested {args.topup_target})"
    )
  else:
    print(f"kept {kept_pos} positives + {kept_neg} negatives across {len(per_repo)} repos")
  print("funnel:", dict(funnel.most_common()))


if __name__ == "__main__":
  main()
