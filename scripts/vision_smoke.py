# scripts/vision_smoke.py
"""Local end-to-end: tarball -> sandboxed preview -> screenshots.
No graph, no LLM. Proves the expensive machinery before integration."""

import asyncio
import tempfile
from pathlib import Path

from app.core.config import get_settings
from app.github.app_auth import get_installation_token  # match your helper
from app.github.client import GitHubClient
from app.vision.capture import capture_routes
from app.vision.sandbox import PreviewConfig, start_preview

REPO = "iashu2k/review-sandbox-ui"
SHA = None  # None = fetch PR head via API; or pin a SHA


async def main() -> None:
  settings = get_settings()
  # adjust to your auth helper
  token = await get_installation_token(settings.github_app_id)
  github = GitHubClient(token=token)
  # adjust to your client
  sha = SHA or await github.get_pr_head_sha(REPO, 1)

  with tempfile.TemporaryDirectory(prefix="vision-smoke-") as tmp:
    repo_dir = await github.fetch_tarball(REPO, sha, Path(tmp))
    cfg = PreviewConfig(
      install_command="echo vendored",
      build_command="npm run build",
      start_command="npm run preview -- --port 4173 --host 0.0.0.0",
      port=4173,
      routes=["/", "/checkout"],
    )
    handle = start_preview(
      repo_dir,
      cfg,
      settings.preview_timeout_seconds,
      settings.preview_mem_limit,
      settings.preview_nano_cpus,
    )
    try:
      shots = await capture_routes(handle.url, cfg.routes, Path("data/screenshots/smoke"))
    finally:
      handle.stop()
    for s in shots:
      print(s.viewport, s.route, "loaded=" + str(s.page_loaded), s.path)


if __name__ == "__main__":
  asyncio.run(main())
