# Golden Multimodal Code-Review Dataset — v0.1.0

## Purpose
Ground-truth set for evaluating the multimodal review chain (sandbox + capture
+ vision analyzer + grounding). Visual cases are the regression oracle for
prompt/model changes in Phase 7.

## Construction
Each visual case is a seeded-defect PR against a minimal Next.js 14 checkout
app, generated mechanically from `fixtures/demo-checkout`
(`scripts/golden/make_visual_fixtures.py`): the template equals the fixture
byte-for-byte minus the seeded defect, and each PR overlay changes exactly one
CSS declaration. Baseline and PR-head snapshots are screenshotted by the
Phase 5 networkless Docker sandbox with viewport-true capture
(`full_page=False`, PNG width == viewport width). Defects are synthetic,
single-cause, and independently verifiable from the diff. Text cases are
harvested real PRs (`data/golden_prs/pool/`, 510 candidates) pending curation.

## Visual cases (all CSS-only, detectable from the PR screenshot alone)
| ID | Defect | Expected finding | Split |
|---|---|---|---|
| vis-layout-overflow-01 | width 320→480px @390px | layout_overflow, right edge, high | holdout |
| vis-contrast-02 | .orderTotal + color #9ca3af | wrong_color_contrast, medium | holdout |
| vis-hidden-content-03 | .orderTotal + display:none | hidden_element, high | holdout |
| vis-broken-alignment-04 | .placeOrder + margin-left 60px | broken_alignment, medium | holdout |
| vis-clean-05 | padding 24→32px | EMPTY observations | holdout |

## Annotation schema
Visual cases are `VisualGoldenExample` (extends `GoldenExample`): standard
envelope (id, source, changed_files, diff_path, expected_outcome,
gold_comments with evidence_requirement + must_not_claim overclaim tripwires)
plus a `visual` block (baseline_shot, pr_shot, viewport, expected_observations
with type/severity_hint/element/edge/evidence_must_mention, expected_empty,
ground_truth_source_line). Matching is semantic: type equality + element
named + evidence tokens cited — not literal string match.

## Split rationale
All 5 visual cases are in `holdout`: they are the proven oracle family and
n=5 is too small to subdivide. The live `fixtures/demo-checkout` fixture
(session 3/4 shots) remains the development-time smoke signal, separate from
the golden holdout. Text cases will be split development/validation/holdout
family-atomic once curated. Split vocabulary follows `app/eval/golden_schemas.py`.

## Generators
Vision model: openai/gpt-4o-mini (won the seeded-defect bake-off). Prompt:
analysis_prompt_v1. Artifact hashes + versions: `data/golden/manifest.json`.

## Known limitations
Small n (5 visual), single framework (Next.js), single route, synthetic seeded
defects, English only, mobile-primary annotation. Contrast/alignment evidence
tokens are first-pass and tunable in Phase 7.

## License
Same as repository.
