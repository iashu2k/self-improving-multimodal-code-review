from __future__ import annotations

import argparse
import struct
from collections.abc import Iterable
from pathlib import Path

from playwright.sync_api import sync_playwright

VIEWPORTS = {
  "mobile": (390, 844),
  "desktop": (1440, 900),
}


def _read_png_width(path: Path) -> int:
  """
  Read the PNG header (IHDR) to extract the pixel width. This avoids
  trusting whatever Playwright reports and ensures artifact width really
  equals viewport width.
  """
  with path.open("rb") as f:
    header = f.read(24)
  # PNG signature (8 bytes) + IHDR chunk (length+type+data).
  # Width is a 4-byte big-endian int at bytes 16–19.
  width = struct.unpack(">I", header[16:20])[0]
  return width


def capture_routes(
  url_base: str,
  routes: Iterable[str],
  out_dir: Path,
  viewport_names: Iterable[str],
) -> list[tuple[str, str]]:
  """
  Capture screenshots for each route and viewport.

  Returns a list of tuples (route_slug, viewport_name), one per screenshot.
  """
  out_dir.mkdir(parents=True, exist_ok=True)

  with sync_playwright() as p:
    browser = p.chromium.launch()
    try:
      page = browser.new_page()
      results: list[tuple[str, str]] = []

      for route in routes:
        slug = route.strip("/").replace("/", "_") or "root"
        for vp_name in viewport_names:
          if vp_name not in VIEWPORTS:
            raise ValueError(f"unknown viewport '{vp_name}'")

          width, height = VIEWPORTS[vp_name]
          page.set_viewport_size({"width": width, "height": height})

          url = f"{url_base}{route}"
          page.goto(url, wait_until="networkidle")

          filename = f"{slug}_{vp_name}.png"
          path = out_dir / filename

          page.screenshot(path=str(path), full_page=False)

          png_width = _read_png_width(path)
          if png_width != width:
            raise RuntimeError(
              f"viewport-width invariant failed for {path}: "
              f"PNG width {png_width} != viewport width {width}"
            )

          results.append((slug, vp_name))

      return results
    finally:
      browser.close()


def main() -> None:
  parser = argparse.ArgumentParser(
    description="Capture route screenshots with strict viewport widths."
  )
  parser.add_argument(
    "--url-base",
    required=True,
    help="Base URL, e.g. http://127.0.0.1:3000",
  )
  parser.add_argument(
    "--routes",
    required=True,
    help="Comma-separated list of routes, e.g. /checkout,/other",
  )
  parser.add_argument(
    "--out",
    required=True,
    help="Output directory for screenshots",
  )
  parser.add_argument(
    "--viewports",
    default="mobile",
    help="Comma-separated viewport names (mobile,desktop). Default: mobile.",
  )

  args = parser.parse_args()

  url_base = args.url_base
  routes = [r for r in args.routes.split(",") if r]
  out_dir = Path(args.out)
  viewport_names = [v for v in args.viewports.split(",") if v]

  capture_routes(url_base, routes, out_dir, viewport_names)


if __name__ == "__main__":
  main()
