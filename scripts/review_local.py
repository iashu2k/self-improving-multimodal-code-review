import argparse
import asyncio
import subprocess
from pathlib import Path

from app.core.config import settings
from app.github.diff_parser import parse_unified_diff
from app.llm.openrouter_client import OpenRouterClient
from app.llm.reviewer import review_diff


def get_diff(repo_path: str, base: str, head: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "diff", f"{base}...{head}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--title", default="Local review run")
    parser.add_argument("--body", default="")
    parser.add_argument("--out", default="data/processed/review.json")
    args = parser.parse_args()

    if not settings.openrouter_review_model:
        raise SystemExit("Set OPENROUTER_REVIEW_MODEL in .env first")

    diff_text = get_diff(args.repo_path, args.base, args.head)
    files = parse_unified_diff(diff_text)
    print(f"Parsed {len(files)} changed files")

    client = OpenRouterClient()
    try:
        result = await review_diff(
            files=files,
            pr_title=args.title,
            pr_body=args.body,
            client=client,
            model=settings.openrouter_review_model,
        )
    finally:
        await client.aclose()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result.model_dump_json(indent=2))
    print(
        f"Wrote {out_path} — {len(result.comments)} comments, "
        f"should_post={result.should_post_review}"
    )


if __name__ == "__main__":
    asyncio.run(main())
