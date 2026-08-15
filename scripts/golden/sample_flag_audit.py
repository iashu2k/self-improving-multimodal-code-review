"""Sample golds for the requires_repo_context flag audit.

baseline_b failed to beat the diff-only floor, and 0 validation golds are
labeled requires_repo_context. Either the labels are right (the recall
ceiling is behavioral, not informational — see the generator policy) or
the flag defaulted to False during LLM drafting and never got audited.

This script samples N golds (fixed seed — reproducible) and writes one
review card per gold: the real reviewer comment, the evidence
requirement, and the diff hunk around the gold line. For each card the
question is: "Could a reviewer write THIS comment seeing only this diff
and the PR title/body?"

Verdicts:
  diff_sufficient    — motivated entirely by what the diff shows
  needs_repo_context — requires code outside the diff (callers,
                       definitions, related modules) — retrievable
  needs_external     — requires issues/CI/prior PRs/product knowledge —
                       NOT retrievable from a repo snapshot
  unclear            — can't tell

Fill the Verdict line per card, then tally:
  rg "^Verdict:" data/processed/flag_audit_validation.md | sort | uniq -c

Usage:
  uv run python scripts/golden/sample_flag_audit.py            # 10 cards
  uv run python scripts/golden/sample_flag_audit.py --n 31     # all validation golds
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from app.evals.judge import _excerpt  # reuse the hunk-aware excerpting

GOLDEN = Path("data/golden")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--split", default="validation")
  parser.add_argument("--n", type=int, default=10)
  parser.add_argument("--seed", type=int, default=42)
  parser.add_argument("--out", default=None)
  args = parser.parse_args()

  out = Path(args.out or f"data/processed/flag_audit_{args.split}.md")

  golds = []
  for p in sorted((GOLDEN / "text" / args.split).glob("*/example.json")):
    ex = json.loads(p.read_text())
    for gi, g in enumerate(ex.get("gold_comments", [])):
      golds.append((ex, gi, g))

  sample = random.Random(args.seed).sample(golds, min(args.n, len(golds)))

  cards = [
    f"# requires_repo_context flag audit — {args.split} "
    f"({len(sample)} of {len(golds)} golds, seed {args.seed})\n"
  ]
  for idx, (ex, _gi, g) in enumerate(sample, 1):
    diff_text = (GOLDEN / ex["diff_path"]).read_text()
    excerpt = _excerpt(diff_text, {(g["file_path"], g["line"])}, pad=15)
    comment = g.get("_reviewer_comment_full") or g["issue_summary"]
    cards.append(
      f"## [{idx}] {ex['example_id']} — {g['category']}/{g['severity']}\n\n"
      f"Gold: `{g['file_path']}:{g['line']}`\n\n"
      f"Reviewer comment:\n> {comment}\n\n"
      f"Evidence requirement: {g['evidence_requirement']}\n\n"
      f"```diff\n{excerpt}\n```\n\n"
      f"Link: {g.get('_gold_comment_url', '')}\n\n"
      f"Verdict: \n"
    )

  out.parent.mkdir(parents=True, exist_ok=True)
  out.write_text("\n".join(cards) + "\n")
  print(f"{len(sample)} cards -> {out}")


if __name__ == "__main__":
  main()
