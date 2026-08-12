from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.github.diff_parser import ChangedFile, parse_unified_diff, reviewable_files
from app.llm.openrouter_client import OpenRouterClient
from app.sandbox.runner import SandboxResult, run_pr_in_sandbox
from app.vision.prompts import build_analysis_prompt
from app.vision.schemas import VisionResult, VisualObservation


@dataclass
class GroundedObservation:
  observation: VisualObservation
  file_path: str
  line_numbers: list[int]


@dataclass
class VisionAnalysisResult:
  sandbox: SandboxResult
  per_viewport: dict[str, VisionResult]
  grounded_observations: list[GroundedObservation]


def _read_png_b64(path: Path) -> str:
  data = path.read_bytes()
  return base64.b64encode(data).decode("ascii")


async def _analyze_viewport(
  *,
  screenshot: Path,
  pr_title: str,
  changed_files: list[ChangedFile],
  viewport_label: str,
  viewport_width_px: int,
) -> VisionResult:
  client = OpenRouterClient()
  model = settings.openrouter_vision_model

  prompt = build_analysis_prompt(
    pr_title=pr_title,
    changed_files=[cf.path for cf in reviewable_files(changed_files)],
    viewport_label=viewport_label,
    viewport_width_px=viewport_width_px,
  )

  image_b64 = _read_png_b64(screenshot)

  messages = [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": prompt},
        {
          "type": "input_image",
          "image_url": {
            "url": f"data:image/png;base64,{image_b64}",
          },
        },
      ],
    }
  ]

  vision_schema = VisionResult.model_json_schema()

  structured = await client.chat_structured(
    model=model,
    schema_name="VisionResult",
    json_schema=vision_schema,
    messages=messages,
    temperature=0.0,
    max_tokens=1500,
  )

  return VisionResult.model_validate(structured.content)


def _ground_observations(
  observations: list[VisualObservation],
  changed_files: list[ChangedFile],
) -> list[GroundedObservation]:
  grounded: list[GroundedObservation] = []

  for obs in observations:
    # Simple heuristic grounding: attach each observation to all
    # commentable lines in CSS / frontend files.
    for cf in reviewable_files(changed_files):
      if (
        cf.path.endswith(".css")
        or cf.path.endswith(".scss")
        or cf.path.endswith(".tsx")
        or cf.path.endswith(".jsx")
      ):
        lines = sorted(cf.commentable_lines)
        if lines:
          grounded.append(
            GroundedObservation(
              observation=obs,
              file_path=cf.path,
              line_numbers=lines,
            )
          )
  return grounded


async def analyze_pr_visual(
  *,
  repo_root: Path,
  pr_title: str,
  diff_text: str,
  routes: Iterable[str],
) -> VisionAnalysisResult:
  """
  High-level visual analyzer:

  1. Parse diff to get ChangedFile structures.
  2. Run sandbox+capture for the given routes.
  3. For each viewport screenshot, call the vision model to get VisionResult.
  4. Ground observations to changed lines in the diff (simple heuristic).
  """
  changed_files = parse_unified_diff(diff_text)

  sandbox_out_dir = repo_root / "data" / "processed" / "session4_analyzer"
  sandbox_result = run_pr_in_sandbox(
    repo_path=repo_root,
    routes=list(routes),
    workdir=sandbox_out_dir,
  )

  if not sandbox_result.ok:
    return VisionAnalysisResult(
      sandbox=sandbox_result,
      per_viewport={},
      grounded_observations=[],
    )

  shots_root = sandbox_out_dir / "screenshots"

  per_viewport: dict[str, VisionResult] = {}

  # For now, focus on mobile viewport; you can expand to desktop later.
  mobile_shots = list(shots_root.rglob("*_mobile.png"))
  if mobile_shots:
    mobile_shot = mobile_shots[0]
    per_viewport["mobile"] = await _analyze_viewport(
      screenshot=mobile_shot,
      pr_title=pr_title,
      changed_files=changed_files,
      viewport_label="mobile",
      viewport_width_px=390,
    )

  desktop_shots = list(shots_root.rglob("*_desktop.png"))
  if desktop_shots:
    desktop_shot = desktop_shots[0]
    per_viewport["desktop"] = await _analyze_viewport(
      screenshot=desktop_shot,
      pr_title=pr_title,
      changed_files=changed_files,
      viewport_label="desktop",
      viewport_width_px=1440,
    )

  # Ground all observations across viewports using a simple heuristic.
  all_observations: list[VisualObservation] = []
  for vr in per_viewport.values():
    all_observations.extend(vr.observations)

  grounded = _ground_observations(all_observations, changed_files)

  return VisionAnalysisResult(
    sandbox=sandbox_result,
    per_viewport=per_viewport,
    grounded_observations=grounded,
  )
