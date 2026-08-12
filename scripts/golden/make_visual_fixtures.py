"""Phase 6B-S1a: generate fixtures/golden/{template,cases} from fixtures/demo-checkout.

Why generated, not hand-written: the template must equal demo-checkout
byte-for-byte minus the seeded defect (single-cause oracle), so we derive it
mechanically. Every transform asserts exactly-one substitution — if the
fixture drifts, this fails loudly instead of silently writing a bad case.

Case design (all css-only on app/checkout/checkout.module.css; each defect is
detectable from the PR screenshot ALONE plus PR intent):
  vis-layout-overflow-01  width 320 -> 480px @390px viewport (the proven oracle)
  vis-contrast-02         .orderTotal gains color: #9ca3af (gray on white)
  vis-hidden-content-03   .orderTotal gains display: none
  vis-broken-alignment-04 .placeOrder gains margin-left: 60px (shifted, not overflowing)
  vis-clean-05            .container padding 24 -> 32px (harmless; expected empty)
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "fixtures/demo-checkout"
GOLDEN = ROOT / "fixtures/golden"
TEMPLATE = GOLDEN / "template"
CASES = GOLDEN / "cases"
CSS_REL = Path("app/checkout/checkout.module.css")


def sub_once(pattern: str, repl: str, text: str, label: str) -> str:
  new, n = re.subn(pattern, repl, text, count=1)
  if n != 1 or new == text:
    sys.exit(f"transform '{label}': expected exactly 1 substitution, got {n}")
  return new


def insert_into_rule(text: str, rule: str, declaration: str, label: str) -> str:
  pattern = rf"(\.{rule}\s*\{{[^}}]*?)(\s*\}})"
  return sub_once(pattern, rf"\1\n  {declaration}\2", text, label)


def main() -> None:
  fixture_css = (SRC / CSS_REL).read_text()

  if GOLDEN.exists():
    shutil.rmtree(GOLDEN)
  shutil.copytree(SRC, TEMPLATE)

  # template = fixture minus the seeded defect (480px -> 320px)
  template_css = sub_once(r"width:\s*480px;[^\n]*", "width: 320px;", fixture_css, "template-width")
  if "480px" in template_css:
    sys.exit("template still contains 480px — check fixture drift")
  (TEMPLATE / CSS_REL).write_text(template_css)

  overlays: dict[str, str] = {
    # PR restores the defect exactly as it exists in the live fixture
    "vis-layout-overflow-01": fixture_css,
    "vis-contrast-02": insert_into_rule(
      # white on white: invisible
      template_css,
      "orderTotal",
      "color: #ffffff;",
      "contrast",
    ),
    "vis-hidden-content-03": insert_into_rule(
      template_css, "orderTotal", "display: none;", "hidden"
    ),
    "vis-broken-alignment-04": insert_into_rule(
      insert_into_rule(template_css, "orderTotal", "text-align: center;", "alignment-center"),
      # heading right, total center, button left
      "heading",
      "text-align: right;",
      "alignment-heading",
    ),
    "vis-clean-05": sub_once(
      r"background-color:\s*#2563eb;",
      "background-color: #1d4ed8;",
      template_css,
      "button-color",
    ),
  }

  for case_id, css in overlays.items():
    dst = CASES / case_id / "pr" / CSS_REL
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(css)
    if css == template_css:
      sys.exit(f"{case_id}: overlay identical to template — transform no-op")
    print(f"[{case_id}] overlay -> {dst.relative_to(ROOT)}")

  print(f"template -> {TEMPLATE.relative_to(ROOT)}")
  print("MAKE VISUAL FIXTURES: DONE")


if __name__ == "__main__":
  main()
