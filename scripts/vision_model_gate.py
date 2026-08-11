# scripts/vision_model_gate.py
"""Adoption gate for the vision model: N structured-output calls against a
real screenshot, count schema-valid responses. >=9/10 with retry recovery
required before the model earns OPENROUTER_VISION_MODEL (decision: model as
configuration, not commitment)."""

import argparse
import asyncio
import base64
from pathlib import Path

from app.llm.openrouter_client import OpenRouterClient
from app.vision.schemas import VisionResult

GATE_PROMPT = "Describe any visible layout problems in this UI screenshot."


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", default="data/processed/gate_shot.png")
    parser.add_argument("--runs", type=int, default=10)
    args = parser.parse_args()

    client = OpenRouterClient()  # settings-driven; no kwargs
    b64 = base64.b64encode(Path(args.image).read_bytes()).decode()

    valid = 0
    try:
        for i in range(args.runs):
            try:
                resp = await client.chat_structured(
                    model=args.model,
                    schema_name="vision_result",
                    json_schema=VisionResult.model_json_schema(),
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": GATE_PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                                },
                            ],
                        },
                    ],
                )
                VisionResult.model_validate(resp.content)
                valid += 1
                print(f"run {i + 1}: valid")
            except Exception as exc:
                print(f"run {i + 1}: INVALID — {type(exc).__name__}: {str(exc)[:200]}")
    finally:
        await client.aclose()

    verdict = "PASS" if valid >= args.runs - 1 else "FAIL"
    print(f"\n{valid}/{args.runs} valid — {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
