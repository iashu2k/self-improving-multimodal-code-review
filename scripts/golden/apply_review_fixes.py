"""Apply the reviewed annotation corrections to golden text examples.

Usage:
  uv run python scripts/golden/apply_review_fixes.py                  # apply field fixes
  uv run python scripts/golden/apply_review_fixes.py --accept-rest    # then accept OK examples

FIXES is the mechanical record of the annotation review (LLM-drafted fields,
assistant-reviewed, human-audited on a sample — see DATASET_CARD). Each entry
patches gold_comments[0] in place and replaces the NEEDS HUMAN REVIEW stamp
with the provenance note.

--accept-rest sets the provenance note on every remaining stamped example
NOT in FIXES. Run it only after the human audit sample — it is the bulk
acceptance step, and it is deliberately a separate flag.

Idempotent: re-running applies the same values again. No LLM, no DB.
"""

import argparse
import json
from pathlib import Path

GOLDEN_TEXT_ROOT = Path("data/golden/text")
SPLIT_DIRS = ("development", "validation", "holdout")
STAMP = "NEEDS HUMAN REVIEW"
PROVENANCE = (
  "LLM-drafted (qwen3-coder-next / claude-haiku-4.5), assistant-reviewed, "
  "human-audited on a sample; see DATASET_CARD.md"
)

FIXES: dict[str, dict] = {
  # --- batch 1 ---
  "allenai__olmo__pr_000378": {
    "severity": "medium",
    "evidence_requirement": "The comment must note that after the cached_path migration, R2 and S3 auth both read the same environment variables (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY), so the two backends cannot be configured simultaneously.",
    "must_not_claim": ["claims credentials are logged or exposed"],
  },
  "allenai__olmo__pr_000400": {"category": "maintainability"},
  "allenai__olmo__pr_000605": {"category": "maintainability"},
  "ansible__awx__pr_012073": {
    "severity": "low",
    "category": "maintainability",
    "evidence_requirement": "The comment must note that the method is misnamed: it only adds task dependencies with activity stream disabled, so the name should say that rather than implying failure-chain capture.",
    "must_not_claim": ["claims dependencies are lost or broken"],
  },
  "ansible__awx__pr_015047": {
    "category": "style",
    "evidence_requirement": "The comment must cite the `not in ('kubernetes')` single-element tuple and note that `!= 'kubernetes'` is the clearer equivalent.",
  },
  "ansible__awx__pr_015287": {"category": "bug_risk"},
  # --- batch 2 ---
  "ansible__awx__pr_015576": {"category": "style", "must_not_claim": []},
  "ansible__awx__pr_015742": {
    "must_not_claim": ["claims the token is always printed in plaintext output"]
  },
  "apache__airflow__pr_044279": {"category": "style"},
  "apache__airflow__pr_053368": {"must_not_claim": []},
  "apache__airflow__pr_059224": {"category": "bug_risk"},
  "apache__airflow__pr_059418": {"category": "maintainability"},
  "apache__airflow__pr_059643": {"severity": "medium", "category": "bug_risk"},
  "apache__airflow__pr_059688": {"category": "maintainability"},
  "apache__arrow__pr_048008": {"severity": "medium", "category": "bug_risk"},
  "apache__arrow__pr_048618": {
    "evidence_requirement": "The comment must note that the wheel-content validation for typing stubs cannot pass because the stub generator (dev/update_stub_docstrings.py) does not exist at this revision; the TODO(GH-32609) acknowledges the gap without resolving it."
  },
  # --- batch 3 ---
  "cinnamon__kotaemon__pr_000093": {"category": "bug_risk"},
  "commaai__openpilot__pr_035984": {"severity": "low"},
  "commaai__openpilot__pr_036035": {"category": "bug_risk"},
  # --- batch 4 ---
  "huggingface__transformers__pr_034198": {
    "category": "bug_risk",
    "evidence_requirement": "The comment must cite the manual division `loss = loss / num_items_in_batch` and note that with ignore_index=-100, reduction='mean' excludes padding tokens automatically, while manual division may not.",
  },
  # --- batch 5 ---
  "apache__superset__pr_033626": {"category": "maintainability"},
  "apache__superset__pr_034199": {"category": "maintainability"},
  "apache__superset__pr_035333": {"category": "style"},
  "apache__superset__pr_035508": {"category": "maintainability"},
  "apache__superset__pr_038075": {"category": "maintainability"},
  "apify__crawlee-python__pr_000320": {"category": "style"},
  "apify__crawlee-python__pr_000572": {"category": "bug_risk"},
  "apify__crawlee-python__pr_000637": {"category": "maintainability"},
  "apify__crawlee-python__pr_000905": {"category": "maintainability"},
  "apify__crawlee-python__pr_001086": {"category": "maintainability"},
  "blackjack4494__yt-dlc__pr_000030": {"severity": "low"},
  "blackjack4494__yt-dlc__pr_000101": {"category": "bug_risk"},
  "firecracker-microvm__firecracker__pr_005137": {"category": "bug_risk"},
  "geekan__metagpt__pr_000965": {"category": "maintainability"},
  "ggerganov__llama.cpp__pr_016464": {
    "severity": "medium",
    "evidence_requirement": "The comment must note that expert weights should be cached per block (e.g. via setdefault) and merged only once all expected weights (w1/w2/w3 per expert) are present, rather than deleting cache entries eagerly.",
  },
  "gradio-app__gradio__pr_011300": {"category": "bug_risk"},
  "gradio-app__gradio__pr_011764": {"category": "maintainability"},
  "huggingface__datasets__pr_002500": {"category": "bug_risk"},
  "huggingface__datasets__pr_005701": {"category": "bug_risk"},
}


def find_example(example_id: str) -> Path | None:
  for split in SPLIT_DIRS:
    path = GOLDEN_TEXT_ROOT / split / example_id / "example.json"
    if path.exists():
      return path
  return None


def apply_fixes() -> None:
  applied, missing = 0, []
  for example_id, fields in FIXES.items():
    path = find_example(example_id)
    if path is None:
      missing.append(example_id)
      continue
    example = json.loads(path.read_text())
    example["gold_comments"][0].update(fields)
    example["human_label_notes"] = PROVENANCE
    path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
    applied += 1
  print(f"applied fixes to {applied}/{len(FIXES)} examples")
  if missing:
    print("not found in split dirs (excluded?): ")
    for example_id in missing:
      print(f"  - {example_id}")


def accept_rest() -> None:
  """Set provenance on stamped examples not in FIXES. The bulk acceptance
  step — run after the human audit sample."""
  accepted = 0
  for split in SPLIT_DIRS:
    for path in sorted((GOLDEN_TEXT_ROOT / split).glob("*/example.json")):
      if path.parent.name in FIXES:
        continue
      text = path.read_text()
      if STAMP not in text:
        continue
      example = json.loads(text)
      example["human_label_notes"] = PROVENANCE
      path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n")
      accepted += 1
  print(f"accepted {accepted} remaining stamped examples")


def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--accept-rest", action="store_true")
  args = parser.parse_args()
  if args.accept_rest:
    accept_rest()
  else:
    apply_fixes()


if __name__ == "__main__":
  main()
