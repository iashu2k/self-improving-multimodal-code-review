from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path

from app.core.config import settings
from app.llm.openrouter_client import OpenRouterClient
from app.sandbox.runner import run_pr_in_sandbox
from app.vision.prompts import build_analysis_prompt
from app.vision.schemas import VisionResult


def _read_png_b64(path: Path) -> str:
    data = path.read_bytes()
    return base64.b64encode(data).decode("ascii")


async def main_async() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    fixture_path = repo_root / "fixtures" / "demo-checkout"
    out_dir = repo_root / "data" / "processed" / "session3_e2e"

    print(f"Running sandbox E2E on fixture at {fixture_path}...")
    result = run_pr_in_sandbox(
        repo_path=repo_root,
        routes=["/checkout"],
        workdir=out_dir,
    )

    if not result.ok:
        print("SESSION 3 E2E: FAIL")
        print(f"stage_failed={result.stage_failed}")
        if result.error:
            print(result.error)
        print("install_log:\n", result.install_log)
        print("build_log:\n", result.build_log)
        print("start_log:\n", result.start_log)
        print("capture_log:\n", result.capture_log)
        sys.exit(1)

    # Find the mobile screenshot.
    shots_dir = out_dir / "screenshots"
    mobile_shots: list[Path] = list(shots_dir.rglob("*_mobile.png"))

    if not mobile_shots:
        print("SESSION 3 E2E: FAIL (no mobile screenshots found)")
        sys.exit(1)

    mobile_shot = mobile_shots[0]
    print(f"Using mobile screenshot: {mobile_shot}")

    image_b64 = _read_png_b64(mobile_shot)

    client = OpenRouterClient()
    model = settings.openrouter_vision_model

    prompt = build_analysis_prompt(
        pr_title="Make checkout button full-width",
        changed_files=["app/checkout/checkout.module.css"],
        viewport_label="mobile",
        viewport_width_px=390,
    )

    # Prepare messages for OpenRouter; single user message with text + image.
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

    # Use the pydantic schema from VisionResult.
    vision_schema = VisionResult.model_json_schema()

    structured = await client.chat_structured(
        model=model,
        schema_name="VisionResult",
        json_schema=vision_schema,
        messages=messages,
        temperature=0.0,
        max_tokens=1500,
    )

    # structured.content is a dict; validate into VisionResult.
    vision_result = VisionResult.model_validate(structured.content)

    observations_text = " ".join(
        f"{obs.type} {obs.description} {obs.visual_evidence}" for obs in vision_result.observations
    ).lower()

    print("Vision observations:")
    print(observations_text)

    if (
        not vision_result.observations
        or "button" not in observations_text
        or "right" not in observations_text
    ):
        print("SESSION 3 E2E: FAIL (missing button/right edge localization)")
        sys.exit(1)

    print("SESSION 3 E2E: PASS")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
