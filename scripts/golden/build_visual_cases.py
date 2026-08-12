"""Phase 6B-S1b: materialize the 5 golden visual cases and capture screenshots.

Inputs (from make_visual_fixtures.py):
  fixtures/golden/template/             clean baseline Next.js app
  fixtures/golden/cases/<case_id>/pr/   overlay: only the files the PR changes

Outputs (under data/golden/visual/):
  repos/<case_id>/{baseline,pr}/   materialized apps (gitignored, regenerable)
  diffs/<case_id>.diff             unified diff baseline -> pr
  shots/<case_id>/checkout_<viewport>_{baseline,pr}.png

Each materialized repo IS the app (package.json at its root), so app_subdir=".".

Usage:
  uv run python scripts/golden/build_visual_cases.py --materialize-only
  uv run python scripts/golden/build_visual_cases.py --cases vis-layout-overflow-01
  uv run python scripts/golden/build_visual_cases.py
"""

from __future__ import annotations

import argparse
import difflib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "fixtures/golden/template"
CASES_DIR = ROOT / "fixtures/golden/cases"
OUT = ROOT / "data/golden/visual"
ROUTES = ["/checkout"]

CASES: dict[str, str] = {
  "vis-layout-overflow-01": "Make checkout button full-width",
  "vis-contrast-02": "Soften order-total text color",
  "vis-hidden-content-03": "Fix checkout spacing",
  "vis-broken-alignment-04": "Polish checkout spacing",
  "vis-clean-05": "Refresh checkout button color",
}


def materialize(case_id: str) -> tuple[Path, Path]:
  baseline = OUT / "repos" / case_id / "baseline"
  pr = OUT / "repos" / case_id / "pr"
  for repo in (baseline, pr):
    if repo.exists():
      shutil.rmtree(repo)
    shutil.copytree(TEMPLATE, repo)
  overlay = CASES_DIR / case_id / "pr"
  if not overlay.is_dir():
    raise FileNotFoundError(f"missing overlay: {overlay}")
  for src in sorted(overlay.rglob("*")):
    if src.is_file():
      dst = pr / src.relative_to(overlay)
      dst.parent.mkdir(parents=True, exist_ok=True)
      shutil.copy2(src, dst)
  return baseline, pr


def write_diff(case_id: str, baseline: Path, pr: Path) -> Path:
  overlay = CASES_DIR / case_id / "pr"
  changed = [p.relative_to(overlay) for p in sorted(overlay.rglob("*")) if p.is_file()]
  chunks: list[str] = []
  for rel in changed:
    old = (
      (baseline / rel).read_text().splitlines(keepends=True) if (baseline / rel).exists() else []
    )
    new = (pr / rel).read_text().splitlines(keepends=True) if (pr / rel).exists() else []
    chunks.extend(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))
  diff_path = OUT / "diffs" / f"{case_id}.diff"
  diff_path.parent.mkdir(parents=True, exist_ok=True)
  diff_path.write_text("".join(chunks))
  return diff_path


def capture(case_id: str, repo: Path, kind: str) -> list[Path]:
  from app.sandbox.runner import run_pr_in_sandbox  # local import: docker only here

  workdir = OUT / "_work" / case_id / kind
  workdir.mkdir(parents=True, exist_ok=True)
  result = run_pr_in_sandbox(repo_path=repo, routes=ROUTES, workdir=workdir, app_subdir=".")
  if not result.ok:
    print(f"[{case_id}/{kind}] sandbox FAILED stage={result.stage_failed}: {result.error}")
    for name in ("install_log", "build_log", "start_log", "capture_log"):
      log = getattr(result, name, "")
      if log:
        print(f"--- {name} (tail) ---\n{log[-2000:]}")
    sys.exit(1)
  shots_dir = OUT / "shots" / case_id
  shots_dir.mkdir(parents=True, exist_ok=True)
  placed: list[Path] = []
  for shot_str in result.screenshots:
    shot = Path(shot_str)
    viewport = shot.stem.rsplit("_", 1)[-1]  # checkout_mobile -> mobile
    dst = shots_dir / f"checkout_{viewport}_{kind}.png"
    shutil.copy2(shot, dst)
    placed.append(dst)
  if not placed:
    sys.exit(f"[{case_id}/{kind}] no screenshots captured")
  return placed


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--cases", default=",".join(CASES), help="csv case ids")
  parser.add_argument("--materialize-only", action="store_true", help="skip docker capture")
  args = parser.parse_args()

  if not TEMPLATE.is_dir():
    sys.exit(f"missing template: {TEMPLATE} — run make_visual_fixtures.py first")

  for case_id in [c.strip() for c in args.cases.split(",") if c.strip()]:
    if case_id not in CASES:
      sys.exit(f"unknown case id: {case_id}")
    baseline, pr = materialize(case_id)
    diff_path = write_diff(case_id, baseline, pr)
    print(f"[{case_id}] materialized + diff -> {diff_path.relative_to(ROOT)}")
    if args.materialize_only:
      continue
    for kind, repo in (("baseline", baseline), ("pr", pr)):
      for p in capture(case_id, repo, kind):
        print(f"[{case_id}/{kind}] shot -> {p.relative_to(ROOT)}")
  print("BUILD VISUAL CASES: DONE")


if __name__ == "__main__":
  main()
