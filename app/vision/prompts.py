"""Prompts for the Phase 5 vision analyzer.

Provenance — build_analysis_prompt() is NOT a first guess. It won a
seeded-defect bake-off (Session 2, Phase 5) against a generic prompt:

  * Seeded defect: CheckoutButton width 320px -> 480px in a 390px mobile
    viewport (PR "Make checkout button full-width").
  * Generic prompt ("examine this page"): 0 detections across 4 models,
    including qwen2.5-vl-72b at 10/10 schema validity. Schema-valid
    responses proved nothing about usefulness.
  * This prompt (PR title + changed files + viewport-edge guard):
    gpt-4o-mini detected the clip AND localized it correctly ("right
    edge"); gemini-2.5-flash-lite detected but mislocalized ("bottom");
    qwen/gemma stayed blind.

Change this text only with a re-run of the seeded bake-off — the wording
(the edge guard, the "cite concrete visual evidence" demand, the explicit
empty-observations escape hatch) is load-bearing, and the grounding chain
downstream (generator + critic) assumes evidence phrased this way.
"""

from __future__ import annotations


def build_analysis_prompt(
    *,
    pr_title: str,
    changed_files: list[str],
    viewport_label: str,
    viewport_width_px: int,
) -> str:
    """Build the intent-conditioned screenshot-analysis prompt.

    Args:
      pr_title: The pull request title — tells the model what the change
        was *trying* to do, which it checks against what it sees.
      changed_files: Repo-relative paths touched by the diff. Gives the
        model the code-side locus so its evidence can be grounded back
        to changed lines by the generator.
      viewport_label: Human-readable label ("mobile", "desktop") — included
        so multi-viewport reviews keep observations attributable.
      viewport_width_px: The screenshot's pixel width MUST equal this
        (asserted by the caller; a full_page capture that grew beyond the
        viewport silently hides edge-clipping — the exact failure this
        prompt exists to detect).
    """
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
        "or bottom). If the page looks correct, return empty observations."
    )
