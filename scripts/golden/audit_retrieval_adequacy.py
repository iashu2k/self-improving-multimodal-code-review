"""Retrieval-adequacy audit: does hybrid_retrieve deliver the context
that repo-context-dependent gold comments need?

For every validation example with a requires_repo_context gold, runs the
same retrieve_contexts path baseline_b uses and prints, side by side:
  - each gold's file:line and evidence_requirement (what the model must cite)
  - the retrieved chunks (file::symbol, lines, first 200 chars)

Read it as: if the chunk that would ground the gold's evidence isn't in
the list, it's a RETRIEVAL failure (fix queries/top-k/chunking). If it
is, it's a MODEL/PROMPT failure (the model had the evidence and didn't
use it). That bisects where baseline_b's missing recall lives.

Embeddings only — no generation. Cost: pennies.

Usage:
  uv run python scripts/golden/audit_retrieval_adequacy.py
  uv run python scripts/golden/audit_retrieval_adequacy.py --split holdout  # holdout day
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import settings
from app.db.session import get_session_maker
from app.evals.run import retrieve_contexts
from app.github.diff_parser import parse_unified_diff
from app.llm.openrouter_client import OpenRouterClient

GOLDEN = Path("data/golden")


async def main(split: str) -> None:
  targets = []
  for p in sorted((GOLDEN / "text" / split).glob("*/example.json")):
    ex = json.loads(p.read_text())
    golds = [g for g in ex.get("gold_comments", []) if g.get("requires_repo_context")]
    if golds:
      targets.append((ex, golds))
  print(f"{len(targets)} {split} examples with repo-context golds\n")

  llm = OpenRouterClient()
  session_maker = get_session_maker()
  async with session_maker() as session:
    for ex, golds in targets:
      diff_text = (GOLDEN / ex["diff_path"]).read_text()
      changed_files = parse_unified_diff(diff_text)
      contexts = await retrieve_contexts(
        changed_files, session, ex["snapshot_id"], llm, settings.openrouter_embedding_model
      )
      print(f"=== {ex['example_id']} (snapshot {ex['snapshot_id']})")
      for g in golds:
        print(f"  GOLD {g['file_path']}:{g['line']} [{g['category']}]")
        print(f"       must cite: {g['evidence_requirement'][:200]}")
      for c in contexts:
        print(f"  CTX  {c.file_path}::{c.symbol} lines {c.start_line}-{c.end_line}")
        print(f"       {c.content[:200]!r}")
      print()


if __name__ == "__main__":
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--split", default="validation")
  args = parser.parse_args()
  asyncio.run(main(args.split))
