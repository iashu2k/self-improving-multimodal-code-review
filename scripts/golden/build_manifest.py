"""Phase 6B-S3: build data/golden/manifest.json.

Reads data/golden/visual/annotations/*.json (VisualGoldenExample) and
data/golden/text/{development,validation,holdout}/*/example.json
(GoldenExample). Split is taken from each example's own `split` field
(single source of truth). Hashes annotations, shots, and diffs (sha256)
so artifact and label drift are both detectable.

Text examples are read ONLY from the three split directories — anything
else under data/golden/text/ (e.g. _excluded/) is ignored by design.

Fails loudly on missing artifacts, unassigned splits, or text examples
still stamped NEEDS HUMAN REVIEW.
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
TEXT_SPLIT_DIRS = ("development", "validation", "holdout")
DATASET_VERSION = "0.2.0"
SCHEMA_VERSION = "1"
PROMPT_VERSION = "analysis_prompt_v4.1"
REVIEW_STAMP = "NEEDS HUMAN REVIEW"


def sha256(path: Path) -> str:
  return hashlib.sha256(path.read_bytes()).hexdigest()


def split_name(split) -> str:
  """Enum-safe split string: works for str-Enum, plain Enum, and raw str."""
  return getattr(split, "value", split) if not isinstance(split, str) else split


def vision_model() -> str:
  from app.core.config import settings

  return settings.openrouter_vision_model


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
        "split": split_name(ex.split),
        "viewport": ex.visual.viewport,
        "expected_empty": ex.visual.expected_empty,
        "paths": {
          "annotation": f"visual/annotations/{ann_path.name}",
          "diff": ex.diff_path,
          "baseline_shot": ex.visual.baseline_shot,
          "pr_shot": ex.visual.pr_shot,
        },
        "sha256": {
          "annotation": sha256(ann_path),
          "diff": sha256(diff),
          "baseline_shot": sha256(base),
          "pr_shot": sha256(pr),
        },
      }
    )

  if TEXT.is_dir():
    for split_dir in TEXT_SPLIT_DIRS:
      for ann_path in sorted((TEXT / split_dir).glob("*/example.json")):
        ex = GoldenExample.model_validate_json(ann_path.read_text())
        if ex.split is None:
          sys.exit(f"{ex.example_id}: no split assigned")
        if REVIEW_STAMP in (ex.human_label_notes or ""):
          sys.exit(f"{ex.example_id}: still stamped {REVIEW_STAMP}")
        diff = need(GOLDEN / ex.diff_path, ex.example_id)
        cases.append(
          {
            "id": ex.example_id,
            "kind": "text",
            "split": split_name(ex.split),
            "paths": {
              "annotation": str(ann_path.relative_to(GOLDEN)),
              "diff": ex.diff_path,
            },
            "sha256": {
              "annotation": sha256(ann_path),
              "diff": sha256(diff),
            },
          }
        )

  if not cases:
    sys.exit("no golden cases found — run annotate_visual_cases.py first")

  by_split: dict[str, int] = {}
  for c in cases:
    key = f"{c['kind']}:{c['split']}"
    by_split[key] = by_split.get(key, 0) + 1

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
