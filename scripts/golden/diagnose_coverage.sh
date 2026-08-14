#!/usr/bin/env bash
# Coverage diagnosis for Phase 7.2 curation.
#
# For each example in curation_coverage.csv:
#   1. prints the PR's changed_files from example.json
#   2. checks each against the snapshot's indexed code_chunks.file_path
#      INDEXED  = exact path match in the index
#      MISSING  = not indexed; prints up to 3 index paths sharing the
#                 basename ("near" lines) — if a near line is the same
#                 path with a prefix, it's a format mismatch, not truncation
#   3. prints a sample of what the index actually contains
#
# Run from the repo root:
#   bash scripts/golden/diagnose_coverage.sh
#   bash scripts/golden/diagnose_coverage.sh path/to/other_coverage.csv

set -euo pipefail

CSV=${1:-data/processed/curation/curation_coverage.csv}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# 1) Dump indexed file_paths for every snapshot referenced in the CSV
SNAP_IDS=$(tail -n +2 "$CSV" | cut -d, -f2 | sort -u | tr '\n' ' ')
echo "dumping index paths for snapshots: $SNAP_IDS"

# shellcheck disable=SC2086
uv run python - "$TMP" $SNAP_IDS <<'PY'
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select

from app.db.models.repo_index import CodeChunk
from app.db.session import get_session_maker

out = Path(sys.argv[1])
sids = [int(a) for a in sys.argv[2:]]


async def main() -> None:
  sm = get_session_maker()
  async with sm() as s:
    for sid in sids:
      paths = (
        await s.scalars(
          select(CodeChunk.file_path).where(CodeChunk.snapshot_id == sid).distinct()
        )
      ).all()
      (out / f"snapshot_{sid}.txt").write_text("\n".join(sorted(paths)) + "\n")
      print(f"  snapshot {sid}: {len(paths)} distinct paths", file=sys.stderr)


asyncio.run(main())
PY

# 2) Per example: changed files vs index
tail -n +2 "$CSV" | while IFS=, read -r example_id snapshot_id coverage nfiles; do
  echo
  echo "=== $example_id (snapshot $snapshot_id, coverage $coverage, $nfiles changed)"

  ex_json=$(find data/golden/text -maxdepth 3 -name example.json -path "*/$example_id/*" | head -1)
  if [[ -z "$ex_json" ]]; then
    echo "  example.json not found on disk"
    continue
  fi

  index_file="$TMP/snapshot_$snapshot_id.txt"
  total=$(wc -l <"$index_file" | tr -d ' ')

  jq -r '.changed_files[]' "$ex_json" | while read -r f; do
    if grep -qxF "$f" "$index_file"; then
      echo "  INDEXED   $f"
    else
      echo "  MISSING   $f"
      grep -F "$(basename "$f")" "$index_file" | head -3 | sed 's/^/      near: /' || true
    fi
  done

  echo "  --- index sample (first 10 of $total indexed paths)"
  head -10 "$index_file" | sed 's/^/      /'
done
