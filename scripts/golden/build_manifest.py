"""Phase 6B-S3: build data/golden/manifest.json.

Reads data/golden/visual/annotations/*.json (VisualGoldenExample). Split is
taken from each example's own `split` field (single source of truth). Hashes
shots + diffs (sha256) so artifact drift is detectable. If
data/golden/text/ exists, its GoldenExample JSONs are folded in too.
Fails loudly on missing artifacts or unassigned splits.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "data/golden"
VISUAL = GOLDEN / "visual"
TEXT = GOLDEN / "text"
DATASET_VERSION = "0.1.0"
SCHEMA_VERSION = "1"
PROMPT_VERSION = "analysis_prompt_v4.1"


def sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def vision_model() -> str:
  try:
    from app.core.config import settings

    return settings.openrouter_vision_model or "openai/gpt-4o-mini"
  except Exception:
    return "openai/gpt-4o-mini"  # handover 2.2 winner; fallback


def need(path: Path, case_id: str) -> Path:
  if not path.exists():
    sys.exit(f"{case_id}: missing artifact {path.relative_to(GOLDEN)}")
  return path


def main() -> None:
  from app.eval.golden_schemas import GoldenExample
  from app.eval.visual_schemas import VisualGoldenExample

  cases: list[dict] = []

  for ann_path in sorted((VISUAL / "annotations").glob("*.json")):
    ex = VisualGoldenExample.model_validate_json(ann_path.read_text())
    if ex.split is None:
      sys.exit(f"{ex.example_id}: no split assigned")
    diff = need(GOLDEN / ex.diff_path, ex.example_id)
    base = need(GOLDEN / ex.visual.baseline_shot, ex.example_id)
    pr = need(GOLDEN / ex.visual.pr_shot, ex.example_id)
    cases.append(
      {
        "id": ex.example_id,
        "kind": "visual",
        "split": str(ex.split),
        "viewport": ex.visual.viewport,
        "expected_empty": ex.visual.expected_empty,
        "paths": {
          "annotation": f"visual/annotations/{ann_path.name}",
          "diff": ex.diff_path,
          "baseline_shot": ex.visual.baseline_shot,
          "pr_shot": ex.visual.pr_shot,
        },
        "sha256": {
          "diff": sha256(diff),
          "baseline_shot": sha256(base),
          "pr_shot": sha256(pr),
        },
      }
    )

  if TEXT.is_dir():
    for ann_path in sorted(TEXT.rglob("*.json")):
      ex = GoldenExample.model_validate_json(ann_path.read_text())
      if ex.split is None:
        sys.exit(f"{ex.example_id}: no split assigned")
      diff = need(GOLDEN / ex.diff_path, ex.example_id)
      cases.append(
        {
          "id": ex.example_id,
          "kind": "text",
          "split": str(ex.split),
          "paths": {
            "annotation": str(ann_path.relative_to(GOLDEN)),
            "diff": ex.diff_path,
          },
          "sha256": {"diff": sha256(diff)},
        }
      )

  if not cases:
    sys.exit("no golden cases found — run annotate_visual_cases.py first")

  by_split: dict[str, int] = {}
  for c in cases:
    by_split[c["split"]] = by_split.get(c["split"], 0) + 1

  manifest = {
    "dataset_version": DATASET_VERSION,
    "created": date.today().isoformat(),
    "generators": {
      "vision_model": vision_model(),
      "prompt_version": PROMPT_VERSION,
      "schema_version": SCHEMA_VERSION,
      "capture": "viewport-true, full_page=False, PNG width == viewport width",
    },
    "cases": cases,
    "totals": {"cases": len(cases), "by_split": by_split},
  }
  out = GOLDEN / "manifest.json"
  out.write_text(json.dumps(manifest, indent=2) + "\n")
  print(f"manifest -> {out.relative_to(ROOT)} ({len(cases)} cases)")


if __name__ == "__main__":
  main()
