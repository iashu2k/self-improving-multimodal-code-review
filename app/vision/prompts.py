"""Prompts for the Phase 5/6B vision analyzer.

Provenance — build_analysis_prompt() is NOT a first guess. It won a
seeded-defect bake-off (Session 2, Phase 5) against a generic prompt:

  * Seeded defect: CheckoutButton width 320px -> 480px in a 390px mobile
    viewport (PR "Make checkout button full-width").
  * Generic prompt ("examine this page"): 0 detections across 4 models,
    including qwen2.5-vl-72b at 10/10 schema validity. Schema-valid
    responses proved nothing about usefulness.
  * v1 (PR title + changed files + viewport-edge guard): gpt-4o-mini
    detected the clip AND localized it correctly ("right edge");
    gemini-2.5-flash-lite detected but mislocalized ("bottom");
    qwen/gemma stayed blind.
  * v2 (Phase 6B): appended anti-confabulation rules after the golden
    eval showed phantom observations on CLEAN pages (invented "cart
    items", a "promotional banner", a "$50.00" price). v1 wording kept
    byte-identical; rules paragraph added.
  * v3 (Phase 6B close-out): build_comparison_prompt added. Single-shot
    analysis hit its ceiling — the model cannot judge "normal" without a
    reference (phantom bottom-edge clipping on clean pages) and cannot
    detect REMOVED content at all (vis-hidden-content-03 returned empty
    on a page missing its order total). Regression is inherently
    comparative; the golden set ships BEFORE shots for exactly this.
  * v4.1 : sonnet-4.5 twice hallucinated bottom-fold clipping from a
    padding diff on a short page; added the full-page-height rule.

Change either prompt only with a re-run of the golden suite
(scripts/eval/run_visual_golden.py must go 5/5) — the wording is
load-bearing, and the grounding chain downstream assumes evidence
phrased this way.
"""

from __future__ import annotations

PROMPT_VERSION = "analysis_prompt_v4"


def build_analysis_prompt(
  *,
  pr_title: str,
  changed_files: list[str],
  viewport_label: str,
  viewport_width_px: int,
) -> str:
  """Single-shot AFTER-only prompt (v2). Prefer build_comparison_prompt
  whenever a baseline screenshot is available."""
  files = ", ".join(changed_files) if changed_files else "(unknown)"
  return (
    f"Context: a pull request titled '{pr_title}' changed "
    f"{files}. This screenshot shows the page rendered AFTER the "
    f"change, in a {viewport_width_px}px-wide {viewport_label} viewport. "
    "IMPORTANT: the image edges ARE the viewport edges — any element cut "
    "off by the image boundary is a real layout defect, not image "
    "cropping. "
    "Inspect for: (1) elements overflowing or clipped at the viewport "
    "edges, (2) unreadable text contrast, (3) hidden or missing content, "
    "(4) visibly broken alignment. For each issue, cite concrete visual "
    "evidence (what is clipped, where, at which edge — left, right, top, "
    "or bottom). If the page looks correct, return empty observations. "
    "Rules: report ONLY issues visible in THIS screenshot. Never describe "
    "elements that are not present (for example cart items, banners, or "
    "images that do not exist on the page) and never invent prices, "
    "labels, or pixel measurements. If you refer to text, quote it "
    "exactly as shown. Only claim clipping when an element is clearly cut "
    "by the image edge AND you can name that element and the edge. "
    "A clean page is a valid result: if nothing is visibly wrong, "
    "return empty observations."
  )


def build_comparison_prompt(
  *,
  pr_title: str,
  changed_files: list[str],
  diff_text: str,
  viewport_label: str,
  viewport_width_px: int,
) -> str:
  """BEFORE/AFTER regression prompt (v4.1: diff-anchored + short-page rule).


  v3 showed even gpt-4o free-scans and confabulates ("Total clipped" on a
  page with no total). v4 hands the model the actual diff hunks so it
  verifies the visual CONSEQUENCES of specific changed lines instead of
  searching for generic defects.


  v4.1: sonnet-4.5 twice hallucinated bottom-fold clipping on the clean
  case — it read the padding diff (24->32px), reasoned "content pushed
  down", and reported the button clipped at the bottom edge on a page
  that is simply short (viewport-true capture, full_page=False, leaves
  empty space below the content). Added the full-page-height rule:
  bottom-edge clipping requires content visibly cut by the bottom image
  boundary, never inferred from spacing/padding diffs.
  """
  files = ", ".join(changed_files) if changed_files else "(unknown)"
  return (
    f"A pull request titled '{pr_title}' changed {files}. You are shown "
    f"two screenshots of the same page in a {viewport_width_px}px-wide "
    f"{viewport_label} viewport: BEFORE (base branch) and AFTER (with "
    "this PR). "
    "The PR diff:\n"
    "```diff\n"
    f"{diff_text}\n"
    "```\n"
    "Use the diff to know exactly which elements and CSS properties "
    "changed, then verify the visual CONSEQUENCES in the images. Report "
    "a regression only when the changed code's effect is actually "
    "visible: (1) elements overflowing or clipped at the viewport edges, "
    "(2) text contrast that became unreadable, (3) content present "
    "BEFORE that is missing or hidden AFTER, (4) alignment that visibly "
    "broke relative to BEFORE. "
    "IMPORTANT: the image edges ARE the viewport edges — an element cut "
    "off by the image boundary is a real layout defect, not image "
    "cropping. "
    "For each regression, cite concrete visual evidence: what changed, "
    "where, and (if clipped) at which edge — left, right, top, or "
    "bottom. "
    "Rules: report only differences visible between THESE two images. "
    "Describe only elements that are actually present in the images; "
    "never invent elements, prices, labels, or pixel measurements, and "
    "quote any text exactly as shown. If a diff change has no visible "
    "effect, do not report it. Do not report intentional improvements "
    "that match the PR title, and do not report issues already present "
    "in the BEFORE image. "
    "The images show the FULL page height: empty space below the "
    "content means the page is simply short — never infer bottom-edge "
    "clipping from spacing or padding changes; report bottom-edge "
    "clipping only when content is visibly cut by the bottom image "
    "boundary. "
    "If the AFTER page looks correct, return empty observations — a "
    "clean page is a valid result."
  )
