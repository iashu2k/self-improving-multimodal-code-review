"""Phase 6B-S2: write the 5 visual golden annotations programmatically.

Ground truth is DERIVED, not hand-typed: defect line numbers are located by
scanning the generated overlay css. Run after make_visual_fixtures.py.
Shots/diffs don't need to exist yet — path integrity is enforced later by
build_manifest.py and tests/test_golden_manifest.py.

Output: data/golden/visual/annotations/<case_id>.json (VisualGoldenExample).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "fixtures/golden/cases"
OUT = ROOT / "data/golden/visual/annotations"
CSS_REL = "app/checkout/checkout.module.css"

DEFECT_MARKER = {
  "vis-layout-overflow-01": "width: 480px",
  "vis-contrast-02": "#ffffff",
  "vis-hidden-content-03": "display: none",
  "vis-broken-alignment-04": "text-align: right",
  "vis-clean-05": "#1d4ed8",
}

SPECS = [
  {
    "id": "vis-layout-overflow-01",
    "pr_title": "Make checkout button full-width",
    "severity": "high",
    "obs": [
      {
        "type": "layout_overflow",
        "severity_hint": "high",
        "element": "Place order button",
        "edge": "right",
        "evidence_must_mention": ["button", "right"],
      }
    ],
    "issue_summary": "Checkout button overflows the 390px mobile viewport",
    "evidence_requirement": "cites the Place order button clipped at the right edge",
    "must_not_claim": ["button is missing", "page failed to load"],
    "rationale": "width 320->480px in a 390px viewport clips the button at the right edge",
  },
  {
    "id": "vis-contrast-02",
    "pr_title": "Update checkout styles",
    "severity": "high",
    "obs": [
      {
        "type": "wrong_color_contrast",
        "severity_hint": "high",
        "element": "order total text",
        "edge": None,
        "evidence_must_mention": ["total"],
      }
    ],
    "issue_summary": "Order-total text is invisible (white on white)",
    "evidence_requirement": "cites the order-total text blending into the background",
    "must_not_claim": ["page failed to load"],
    "rationale": (
      "#ffffff on white renders the total invisible — unambiguous, intent can't license it"
    ),
  },
  {
    "id": "vis-hidden-content-03",
    "pr_title": "Fix checkout spacing",
    "severity": "high",
    "obs": [
      {
        "type": "hidden_element",
        "severity_hint": "high",
        "element": "order total text",
        "edge": None,
        "evidence_must_mention": ["order", "missing"],
      }
    ],
    "issue_summary": "Order total is no longer visible on the checkout page",
    "evidence_requirement": (
      "cites that the order total is absent while heading and button render"
    ),
    "must_not_claim": ["page failed to load"],
    "rationale": (
      "display:none on .orderTotal hides pricing on a checkout page; "
      "a spacing-titled PR gives no license to remove it"
    ),
  },
  {
    "id": "vis-broken-alignment-04",
    "pr_title": "Polish checkout styling",
    "severity": "medium",
    "obs": [
      {
        "type": "broken_alignment",
        "severity_hint": "medium",
        "element": "heading",
        "edge": None,
        "evidence_must_mention": ["align"],
      }
    ],
    "issue_summary": "Heading, total, and button sit at three different alignments",
    "evidence_requirement": "cites the heading pulled right while the button stays left",
    "must_not_claim": ["button overflows"],
    "rationale": "heading right + total center + button left is visibly incoherent",
  },
  {
    "id": "vis-clean-05",
    "pr_title": "Refresh checkout button color",
    "severity": None,
    "obs": [],
    "issue_summary": "",
    "evidence_requirement": "",
    "must_not_claim": [],
    "rationale": "",
    "no_comment_rationale": (
      "subtle brand-color tweak (blue-600 -> blue-700) is a correct, "
      "intentional change; flagging it is over-flagging"
    ),
  },
]


def defect_line(case_id: str) -> int:
  css_path = CASES / case_id / "pr" / CSS_REL
  if not css_path.exists():
    sys.exit(f"{case_id}: missing overlay {css_path} — run make_visual_fixtures.py first")
  marker = DEFECT_MARKER[case_id]
  for n, line in enumerate(css_path.read_text().splitlines(), start=1):
    if marker in line:
      return n
  sys.exit(f"{case_id}: defect marker '{marker}' not found in overlay")


def build_payload(spec: dict) -> dict:
  case_id = spec["id"]
  line = defect_line(case_id)
  expected_empty = not spec["obs"]
  gold_comments = []
  if not expected_empty:
    gold_comments = [
      {
        "file_path": CSS_REL,
        "line": line,
        "category": "ui_regression",
        "severity": spec["severity"],
        "issue_summary": spec["issue_summary"],
        "evidence_requirement": spec["evidence_requirement"],
        "must_not_claim": spec["must_not_claim"],
        "requires_repo_context": False,
        "requires_screenshot": True,
        "rationale": spec["rationale"],
      }
    ]
  return {
    "example_id": case_id,
    "source": "review-sandbox",
    "repository": "local/fixtures",
    "commit_sha": "fixture-local",
    "language": "css",
    "pr_metadata": {"title": spec["pr_title"], "description": "", "url": ""},
    "changed_files": [CSS_REL],
    "diff_path": f"visual/diffs/{case_id}.diff",
    "expected_outcome": "no_comment" if expected_empty else "comment_expected",
    "gold_comments": gold_comments,
    "context_files": [],
    "no_comment_rationale": spec.get("no_comment_rationale", ""),
    "human_label_notes": "",
    "split": "holdout",
    "visual": {
      "baseline_shot": f"visual/shots/{case_id}/checkout_mobile_baseline.png",
      "pr_shot": f"visual/shots/{case_id}/checkout_mobile_pr.png",
      "viewport": "mobile",
      "expected_observations": spec["obs"],
      "expected_empty": expected_empty,
      "ground_truth_source_line": f"{CSS_REL}:{line}",
    },
  }


def main() -> None:
  from app.eval.visual_schemas import VisualGoldenExample

  OUT.mkdir(parents=True, exist_ok=True)
  for spec in SPECS:
    ex = VisualGoldenExample.model_validate(build_payload(spec))
    path = OUT / f"{ex.example_id}.json"
    path.write_text(json.dumps(json.loads(ex.model_dump_json()), indent=2) + "\n")
    line = ex.visual.ground_truth_source_line.rsplit(":", 1)[-1]
    print(f"[{ex.example_id}] {ex.expected_outcome} @ line {line} -> {path.relative_to(ROOT)}")
  print("ANNOTATE VISUAL CASES: DONE")


if __name__ == "__main__":
  main()
