"""Phase 6B close-out / Phase 7 entry: run the vision model against the
golden visual cases and score against annotations.

Matching policy (semantic, not literal):
  - expected observation matches an analyzer observation iff:
      type equal AND
      element has >= 1 significant token in the observation text AND
      every evidence_must_mention phrase appears (case-insensitive) AND
      edge word appears, if the expectation sets one
  - severity_hint mismatch is a WARNING, not a failure (hints are noisy)
  - non-empty case: extra unmatched observations are reported as
    over_flagged but do not fail the case
  - expected_empty case: PASS iff zero observations (hard gate)

Client contract mirrors scripts/sandbox_e2e.py: chat_structured is async,
takes schema_name + json_schema (dict) + messages (content-parts with
input_image), returns an object whose .content dict is validated into
VisionResult. Runs under asyncio.run because the client's CostGuard gets
Redis via get_redis(), which needs a running loop.

Usage:
  uv run python scripts/eval/run_visual_golden.py
  uv run python scripts/eval/run_visual_golden.py --cases vis-layout-overflow-01,vis-clean-05
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLDEN = ROOT / "data/golden"
REPORT = ROOT / "data/processed/visual_golden_eval.json"
VIEWPORT_LABEL = "mobile"
VIEWPORT_WIDTH_PX = 390

_STOP = {"the", "a", "an", "of", "is", "text"}


def content_parts(text: str, image_b64: str) -> list[dict]:
  return [
    {"type": "text", "text": text},
    {"type": "input_image", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
  ]


def build_messages(prompt: str, pr_b64: str, baseline_b64: str | None = None) -> list[dict]:
  parts: list[dict] = []
  if baseline_b64 is not None:
    parts += content_parts(
      "BEFORE image (page rendered from the base branch, before this PR):",
      baseline_b64,
    )
  parts += content_parts(f"AFTER image (page rendered with this PR applied):\n\n{prompt}", pr_b64)
  return [{"role": "user", "content": parts}]


def significant_tokens(phrase: str) -> list[str]:
  return [t for t in phrase.lower().split() if t not in _STOP]


def obs_text(obs) -> str:
  return f"{obs.description} {obs.visual_evidence}".lower()


def matches(expected, obs) -> tuple[bool, list[str]]:
  reasons: list[str] = []
  if str(obs.type) != str(expected.type):
    return False, [f"type {obs.type} != {expected.type}"]
  text = obs_text(obs)
  tokens = significant_tokens(expected.element)
  if tokens and not any(t in text for t in tokens):
    reasons.append(f"no element token of '{expected.element}' in observation")
  for phrase in expected.evidence_must_mention:
    if phrase.lower() not in text:
      reasons.append(f"missing evidence phrase '{phrase}'")
  if expected.edge and expected.edge.lower() not in text:
    reasons.append(f"missing edge '{expected.edge}'")
  if str(obs.severity_hint) != str(expected.severity_hint):
    reasons.append(f"WARN severity {obs.severity_hint} != {expected.severity_hint}")
  hard_fail = [r for r in reasons if not r.startswith("WARN")]
  return (not hard_fail), reasons


def score_case(ex, result) -> dict:
  observations = list(result.observations)
  matched_expected = []
  unmatched_obs = list(observations)
  details = []
  for expected in ex.visual.expected_observations:
    hit = None
    for obs in unmatched_obs:
      ok, reasons = matches(expected, obs)
      if ok:
        hit = obs
        details.extend(r for r in reasons if r.startswith("WARN"))
        break
      details.extend(reasons)
    if hit is not None:
      unmatched_obs.remove(hit)
      matched_expected.append(str(expected.type))
    else:
      details.append(f"NO MATCH for expected {expected.type}")

  if ex.visual.expected_empty:
    passed = len(observations) == 0
  else:
    passed = len(matched_expected) == len(ex.visual.expected_observations)

  return {
    "id": ex.example_id,
    "passed": passed,
    "page_loaded": result.page_loaded,
    "matched": matched_expected,
    "over_flagged": [str(o.type) for o in unmatched_obs] if not ex.visual.expected_empty else [],
    "observation_count": len(observations),
    "observations": [
      {
        "type": str(o.type),
        "severity_hint": str(o.severity_hint),
        "description": o.description,
        "visual_evidence": o.visual_evidence,
      }
      for o in observations
    ],
    "uncertainties": list(result.uncertainties),
    "details": details,
  }


async def evaluate(case_ids: list[str], model_override: str | None = None) -> list[dict]:
  from app.core.config import settings
  from app.eval.visual_schemas import VisualGoldenExample
  from app.llm.openrouter_client import OpenRouterClient
  from app.vision.prompts import build_analysis_prompt, build_comparison_prompt
  from app.vision.schemas import VisionResult

  client = OpenRouterClient()
  model = model_override or settings.openrouter_vision_model
  vision_schema = VisionResult.model_json_schema()

  results = []
  for case_id in case_ids:
    ex = VisualGoldenExample.model_validate_json(
      (GOLDEN / "visual" / "annotations" / f"{case_id}.json").read_text()
    )
    shot = GOLDEN / ex.visual.pr_shot
    pr_b64 = base64.b64encode(shot.read_bytes()).decode("ascii")
    baseline_shot = GOLDEN / ex.visual.baseline_shot
    baseline_b64 = (
      base64.b64encode(baseline_shot.read_bytes()).decode("ascii")
      if baseline_shot.exists()
      else None
    )
    diff_text = (GOLDEN / ex.diff_path).read_text()
    prompt_kwargs = dict(
      pr_title=ex.pr_metadata["title"],
      changed_files=ex.changed_files,
      diff_text=diff_text,
      viewport_label=VIEWPORT_LABEL,
      viewport_width_px=VIEWPORT_WIDTH_PX,
    )
    prompt = (
      build_comparison_prompt(**prompt_kwargs)
      if baseline_b64 is not None
      else build_analysis_prompt(**prompt_kwargs)
    )
    messages = build_messages(prompt, pr_b64, baseline_b64)
    structured = await client.chat_structured(
      model=model,
      schema_name="VisionResult",
      json_schema=vision_schema,
      messages=messages,
      temperature=0.0,
      max_tokens=1500,
    )
    result = VisionResult.model_validate(structured.content)
    scored = score_case(ex, result)
    status = "PASS" if scored["passed"] else "FAIL"
    print(
      f"[{status}] {case_id}: matched={scored['matched']} "
      f"over_flagged={scored['over_flagged']} obs={scored['observation_count']}"
    )
    for o in scored["observations"]:
      print(f"    [{o['type']}/{o['severity_hint']}] {o['description']}")
      print(f"        evidence: {o['visual_evidence']}")
    for u in scored["uncertainties"]:
      print(f"    uncertainty: {u}")
    for d in scored["details"]:
      print(f"    {d}")
    results.append(scored)
  return results


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--cases", default="", help="csv case ids (default: all)")
  parser.add_argument("--model", default=None, help="override vision model")
  args = parser.parse_args()

  manifest = json.loads((GOLDEN / "manifest.json").read_text())
  wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
  case_ids = [
    c["id"]
    for c in manifest["cases"]
    if c["kind"] == "visual" and (not wanted or c["id"] in wanted)
  ]
  if not case_ids:
    sys.exit("no visual cases selected")

  results = asyncio.run(evaluate(case_ids, args.model))

  passed = sum(1 for r in results if r["passed"])
  REPORT.parent.mkdir(parents=True, exist_ok=True)
  REPORT.write_text(
    json.dumps({"passed": passed, "total": len(results), "cases": results}, indent=2) + "\n"
  )
  print(f"\nVISUAL GOLDEN EVAL: {passed}/{len(results)} passed -> {REPORT.relative_to(ROOT)}")
  sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
  main()
