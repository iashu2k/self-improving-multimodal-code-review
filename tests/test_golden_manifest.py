"""Phase 6B-S4: golden dataset integrity gate.

Done-checklist: every case file exists, hash matches, split covers all
cases exactly once. Remove the module-level skipif at 6B close.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from app.eval.visual_schemas import VisualGoldenExample

GOLDEN = Path(__file__).resolve().parents[1] / "data" / "golden"
MANIFEST = GOLDEN / "manifest.json"
VALID_SPLITS = {"development", "validation", "holdout"}

pytestmark = pytest.mark.skipif(
  not MANIFEST.exists(), reason="golden dataset not built yet (6B-S3)"
)


def load_manifest() -> dict:
  return json.loads(MANIFEST.read_text())


def test_every_case_file_exists_and_hash_matches():
  manifest = load_manifest()
  assert manifest["cases"], "manifest has no cases"
  assert manifest["totals"]["cases"] == len(manifest["cases"])
  for case in manifest["cases"]:
    for key, rel in case["paths"].items():
      path = GOLDEN / rel
      assert path.exists(), f"{case['id']}: missing {rel}"
      if key in case["sha256"]:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == case["sha256"][key], f"{case['id']}: hash drift on {rel}"


def test_split_covers_all_cases_exactly_once():
  manifest = load_manifest()
  ids = [c["id"] for c in manifest["cases"]]
  assert len(ids) == len(set(ids)), "duplicate case ids"
  for case in manifest["cases"]:
    assert case["split"] in VALID_SPLITS, f"{case['id']}: bad split {case['split']}"
  assert any(c["split"] == "holdout" for c in manifest["cases"])


def test_annotations_validate_and_match_manifest():
  manifest = load_manifest()
  ann_ids = set()
  for path in sorted((GOLDEN / "visual" / "annotations").glob("*.json")):
    ex = VisualGoldenExample.model_validate_json(path.read_text())
    # empty/non-empty XOR enforced by VisualGroundTruth
    ann_ids.add(ex.example_id)
  visual_manifest_ids = {c["id"] for c in manifest["cases"] if c["kind"] == "visual"}
  assert ann_ids == visual_manifest_ids, "visual annotations and manifest disagree"


def test_visual_defect_lines_point_at_marker():
  """Grounding sanity: each gold comment's RIGHT-side line cites the defect marker.

  Markers are imported from the annotation generator (single source of
  truth). If a defect changes in make_visual_fixtures.py,
  annotate_visual_cases.py fails loudly until its marker is updated —
  and this test follows automatically. No second copy to drift.
  """
  from scripts.golden.annotate_visual_cases import DEFECT_MARKER

  for path in sorted((GOLDEN / "visual" / "annotations").glob("*.json")):
    ex = VisualGoldenExample.model_validate_json(path.read_text())
    if ex.example_id not in DEFECT_MARKER or not ex.gold_comments:
      continue  # clean case has no gold comment to ground
    overlay = (
      Path(__file__).resolve().parents[1]
      / "fixtures/golden/cases"
      / ex.example_id
      / "pr"
      / ex.gold_comments[0].file_path
    )
    lines = overlay.read_text().splitlines()
    assert DEFECT_MARKER[ex.example_id] in lines[ex.gold_comments[0].line - 1], (
      f"{ex.example_id}: gold comment line does not cite the defect"
    )
