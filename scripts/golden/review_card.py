"""Print compact review cards for pre-annotated golden examples.

Usage:
  uv run python scripts/golden/review_card.py --split holdout           # cards
  uv run python scripts/golden/review_card.py --split holdout --next    # first unreviewed

Each card: the anchor line's actual content (production parser), the human
reviewer's verbatim comment, and the drafted fields. Review = scan the card,
edit example.json in place on disagreement, remove the NEEDS HUMAN REVIEW
stamp when satisfied.

No LLM, no DB — pure local reads.
"""

import argparse
import json
import signal
from pathlib import Path

from app.github.diff_parser import parse_unified_diff

signal.signal(signal.SIGPIPE, signal.SIG_DFL)


GOLDEN_TEXT_ROOT = Path("data/golden/text")
STAMP = "NEEDS HUMAN REVIEW"


def anchor_line_content(diff_text: str, file_path: str, line: int) -> str:
  for changed_file in parse_unified_diff(diff_text):
    if changed_file.path != file_path:
      continue
    for hunk in changed_file.hunks:
      for diff_line in hunk.lines:
        if diff_line.new_lineno == line:
          return diff_line.content
  return "<line not found>"


def card(example_path: Path) -> str:
  example = json.loads(example_path.read_text())
  diff_text = (example_path.parent / "diff.patch").read_text()
  lines = [
    f"=== {example['example_id']} ({example.get('split')}, "
    f"{example.get('diff_revision', 'head')}) ===",
    f"PR: {example['pr_metadata'].get('title', '')[:100]}",
  ]
  for gc in example.get("gold_comments", []):
    anchor = f"{gc['file_path']}:{gc['line']}"
    content = anchor_line_content(diff_text, gc["file_path"], gc["line"])
    lines += [
      f"  anchor:  {anchor}",
      f"  line:    {content.strip()[:120]}",
      f"  review:  {gc.get('_reviewer_comment_full', gc['issue_summary'])[:400]}",
      f"  drafted: severity={gc['severity']} category={gc['category']}",
      f"  evidreq: {gc['evidence_requirement'][:300]}",
      f"  rationale: {gc['rationale'][:200]}",
      f"  must_not_claim: {gc['must_not_claim']}",
    ]
  lines.append(f"  file: {example_path}")
  return "\n".join(lines)


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--split", default=None, help="development|validation|holdout")
  parser.add_argument("--next", action="store_true", help="only the first stamped example")
  args = parser.parse_args()

  roots = (
    [GOLDEN_TEXT_ROOT / args.split]
    if args.split
    else [GOLDEN_TEXT_ROOT / s for s in ("holdout", "validation", "development")]
  )
  stamped = sorted(
    p
    for root in roots
    if root.is_dir()
    for p in root.glob("*/example.json")
    if STAMP in p.read_text()
  )
  if not stamped:
    print("Nothing stamped — review complete.")
    return

  targets = stamped[:1] if args.next else stamped
  for path in targets:
    print(card(path))
    print()
  print(f"--- {len(stamped)} stamped remaining ---")


if __name__ == "__main__":
  main()
