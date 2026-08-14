"""Diagnose why anchor resolution fails for a pool example.

Usage:
  uv run python scripts/golden/debug_anchor.py activeloopai__hub__pr_002447

Prints which gate fails: header parse, comment_line range, deleted-line
anchor, or the parser line-set sanity check. No network, no DB.
"""

import json
import re
import sys
from pathlib import Path

POOL_ROOT = Path("data/golden_prs/pool")
HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def main() -> None:
  example_id = sys.argv[1]
  pool_dir = POOL_ROOT / example_id
  metadata = json.loads((pool_dir / "metadata.json").read_text())
  source_row = json.loads((pool_dir / "source_row.json").read_text())

  print(f"example:        {example_id}")
  print(f"file_path:      {source_row.get('file_path')}")
  print(f"candidate_file: {metadata.get('candidate_file')}")
  print(f"comment_line:   {source_row.get('comment_line')!r}")
  print(f"is_negative:    {source_row.get('is_negative')}")

  diff_context = source_row.get("diff_context", "")
  lines = diff_context.split("\n")
  print(f"\ndiff_context lines: {len(lines)}")
  print(f"first line: {lines[0][:100]!r}")

  header = HUNK_HEADER_RE.match(lines[0])
  if header is None:
    print("\nFAIL GATE 1: @@ header did not parse")
    if len(lines) > 1:
      print(f"second line: {lines[1][:100]!r}")
    return
  new_start = int(header.group(3))
  print(f"hunk header:  -{header.group(1)},{header.group(2)} +{new_start},{header.group(4)}")

  body = lines[1:]
  try:
    comment_line = int(source_row["comment_line"])
  except (TypeError, ValueError):
    print(f"\nFAIL: comment_line not an int: {source_row.get('comment_line')!r}")
    return
  print(f"body lines: {len(body)}, comment_line: {comment_line}")
  if not (1 <= comment_line <= len(body)):
    print("\nFAIL GATE 2: comment_line outside body range")
    return

  new_lineno = new_start
  resolved = None
  for i, raw in enumerate(body, start=1):
    if raw.startswith("\\"):
      continue
    if raw.startswith("+"):
      current = new_lineno
      new_lineno += 1
    elif raw.startswith("-"):
      current = None
    else:
      current = new_lineno
      new_lineno += 1
    if i == comment_line:
      resolved = current
      print(f"\nbody line {i}: {raw[:90]!r}")
      break

  if resolved is None:
    print("FAIL GATE 3: comment anchored to a DELETED line (no RIGHT-side number)")
    return
  print(f"resolved absolute line: {resolved}")

  commentable = set(metadata.get("commentable_lines", []))
  right_side = set(metadata.get("right_side_lines", []))
  print(f"in commentable_lines ({len(commentable)} entries): {resolved in commentable}")
  print(f"in right_side_lines ({len(right_side)} entries):   {resolved in right_side}")
  if resolved not in commentable | right_side:
    nearest = sorted(right_side, key=lambda n: abs(n - resolved))[:5]
    print("FAIL GATE 4: resolved line not in parser-computed sets")
    print(f"nearest right-side lines: {nearest}")
    print(f"right_side range: {min(right_side)}..{max(right_side)}" if right_side else "empty")
  else:
    print("\nOK — resolution should have succeeded; re-check curate script")


if __name__ == "__main__":
  main()
