"""Pre-annotate golden text examples: LLM drafts the TODO fields, human reviews.

Usage:
  uv run python scripts/golden/pre_annotate.py --split holdout --limit 3  # smoke
  uv run python scripts/golden/pre_annotate.py --force --include-excluded # full re-judge
  uv run python scripts/golden/pre_annotate.py                            # TODOs only
  uv run python scripts/golden/pre_annotate.py --model anthropic/claude-haiku-4.5

Two-stage per example, one model call:
  1. ISSUE-PRESENCE GATE (head-revision examples only). Reviewer comments
     were written on an EARLIER PR revision; a head diff may already contain
     the fix. v4 rule (calibrated by audit): "applied" requires the
     suggestion's code — or a clear equivalent resolving the CONCERN — in the
     diff's added lines. Disagreement, bare TODOs, and unapplied nits are
     "present"; a deferral WITH a tracking-issue link is a resolution of
     record. Dead issues move to data/golden/text/_excluded/.
     The gate does NOT run on review-revision examples
     (diff_revision == "review_comment_time"): the reviewer wrote the comment
     on that exact code, so presence is guaranteed by construction — asking
     the model would only manufacture false exclusions (observed: 4 revision
     examples excluded by the head-calibrated gate, restored by hand).
  2. DRAFTING: severity / evidence_requirement / rationale / must_not_claim
     from the file's diff section + the anchor line's ACTUAL content
     (production diff parser — decision 17) + the reviewer's verbatim
     comment. Severity rubric imported from the PRODUCTION review prompt.

--include-excluded re-judges _excluded/*/example.json; "present" verdicts
auto-restore to the recorded split. Without --force, already-drafted
examples (no TODO markers) are untouched.

Robustness: max_tokens=2500 (verbose responses truncate at 1200, and at
temperature 0 truncation is deterministic — retries can't help). Per-example
try/except: one failure records to curation_failures.jsonl, never kills the
batch. qwen3-coder-next has a repetition-degeneration failure mode on some
inputs (thousands of repeated \n tokens); --model anthropic/claude-haiku-4.5
handles those stragglers.

Human review stays the gate: drafted examples are stamped in
human_label_notes ("NEEDS HUMAN REVIEW"); human-edited fields are never
overwritten without --force. Ends with a GoldenExample validation census.
"""

import argparse
import asyncio
import json
import shutil
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.eval.golden_schemas import GoldenExample
from app.github.diff_parser import parse_unified_diff
from app.llm.openrouter_client import OpenRouterClient
from app.llm.prompts.review import SYSTEM_PROMPT

GOLDEN_TEXT_ROOT = Path("data/golden/text")
EXCLUDED_ROOT = GOLDEN_TEXT_ROOT / "_excluded"
FAILURES = GOLDEN_TEXT_ROOT / "curation_failures.jsonl"
TODO = "TODO"
CONCURRENCY = 4


class AnnotationDraft(BaseModel):
  issue_status: Literal["present", "applied", "unclear"]
  presence_explanation: str = Field(min_length=5)
  severity: Literal["low", "medium", "high", "critical"]
  evidence_requirement: str = Field(min_length=10)
  rationale: str = Field(min_length=10)
  must_not_claim: list[str] = []


ANNOTATOR_SYSTEM = f"""You are annotating a golden evaluation dataset for a code-review agent.

CRITICAL CONTEXT: the reviewer's comment was written on an EARLIER revision
of this PR. The diff you are shown is the PR at its FINAL head. Authors often
respond to reviewer suggestions; only SOME responses resolve the concern.

STEP 1 — issue_status:
- "applied": the diff's added lines contain code materially identical to the
  reviewer's suggestion block, OR code that clearly resolves the reviewer's
  underlying concern. A deferral with a tracking-issue link (e.g. "TODO:
  GH-12345") is a resolution of record — judge it "applied" only when the
  linked deferral is explicit in the diff.
- "present": the concern is live at head. This INCLUDES: the author not
  taking the suggestion at all; the author changing the code DIFFERENTLY
  without addressing the concern; the author doing the opposite of the
  suggestion; a bare TODO with no tracking issue; and nits that were never
  applied (an unapplied nit is present, severity low).
- "unclear": you cannot tell from the diff alone.
Set presence_explanation to ONE sentence, under 60 words, naming the deciding
evidence. Brevity here keeps the response within the token budget.

STEP 2 — draft fields (fill regardless; used only when status is "present"):
- severity: apply EXACTLY the severity rubric below — the evaluated agent is
  scored against these definitions.
- evidence_requirement: ONE sentence, under 60 words — what a generated
  comment must cite or demonstrate to count as finding this issue. Describe
  the ISSUE as it exists in the diff (name the changed code by content, the
  failure mode or improvement); never reference the reviewer back-and-forth.
- rationale: one sentence — why a human reviewer would leave this comment.
- must_not_claim: 0-3 short strings — specific overclaims BEYOND what the
  reviewer asserted, tied to THIS comment's domain. Good (log-level nitpick):
  "claims the logs leak Wandb credentials". Bad (generic filler — never emit
  unless the reviewer gestured at that risk): "causes crash", "breaks
  functionality", "exposes secrets". Empty list when nothing plausible.

--- production severity rubric ---
{SYSTEM_PROMPT}
"""

REVISION_NOTE = """
NOTE: this diff is the exact revision the reviewer commented on (their
original_commit_id), so the issue is PRESENT by construction. Set
issue_status to "present", write "review-revision example" as the
presence_explanation, and put your effort into STEP 2.
"""


def file_diff_section(diff_text: str, file_path: str) -> str:
  """Extract one file's section from a full unified diff."""
  for section in diff_text.split("diff --git ")[1:]:
    header_line = section.split("\n", 1)[0]
    if f" b/{file_path}" in header_line:
      return "diff --git " + section
  return ""


def anchor_line_content(diff_text: str, file_path: str, line: int) -> str:
  """The anchor line's actual content, via the production parser — the same
  code that defines legal lines for the validator (decision 17)."""
  for changed_file in parse_unified_diff(diff_text):
    if changed_file.path != file_path:
      continue
    for hunk in changed_file.hunks:
      for diff_line in hunk.lines:
        if diff_line.new_lineno == line:
          return diff_line.content
  return ""


def needs_annotation(gold_comment: dict) -> bool:
  return str(gold_comment.get("evidence_requirement", "")).startswith(TODO) or str(
    gold_comment.get("rationale", "")
  ).startswith(TODO)


def record_failure(example_id: str, reason: str) -> None:
  FAILURES.parent.mkdir(parents=True, exist_ok=True)
  with FAILURES.open("a") as fh:
    fh.write(json.dumps({"example_id": example_id, "reason": reason}) + "\n")


def exclude_example(example_path: Path, example_id: str, reason: str) -> None:
  EXCLUDED_ROOT.mkdir(parents=True, exist_ok=True)
  shutil.move(str(example_path.parent), str(EXCLUDED_ROOT / example_id))
  record_failure(example_id, reason)


def restore_example(example_path: Path, example: dict) -> None:
  """Auto-restore a previously excluded example to its recorded split."""
  dest = GOLDEN_TEXT_ROOT / example["split"] / example["example_id"]
  shutil.move(str(example_path.parent), str(dest))


async def annotate_example(
  client: OpenRouterClient, model: str, example_path: Path, force: bool
) -> str:
  """Returns 'drafted' | 'skipped' | 'excluded' | 'restored'."""
  example = json.loads(example_path.read_text())
  example_id = example["example_id"]
  was_excluded = EXCLUDED_ROOT in example_path.parents
  # Review-revision diffs are the exact code the reviewer commented on —
  # presence is guaranteed by construction, so the gate is skipped entirely.
  is_revision = example.get("diff_revision") == "review_comment_time"
  diff_text = (example_path.parent / "diff.patch").read_text()
  changed = False

  for gc in example.get("gold_comments", []):
    if not force and not needs_annotation(gc):
      continue
    section = file_diff_section(diff_text, gc["file_path"])
    user = json.dumps(
      {
        "pr_title": example["pr_metadata"].get("title", ""),
        "anchor": f"{gc['file_path']}:{gc['line']}",
        "anchor_line_content": anchor_line_content(diff_text, gc["file_path"], gc["line"]),
        "reviewer_comment": gc.get("_reviewer_comment_full", gc["issue_summary"]),
        "diff_section": section[:8000],
      }
    )
    response = await client.chat_structured(
      model=model,
      schema_name="AnnotationDraft",
      json_schema=AnnotationDraft.model_json_schema(),
      messages=[
        {
          "role": "system",
          "content": ANNOTATOR_SYSTEM + (REVISION_NOTE if is_revision else ""),
        },
        {"role": "user", "content": user},
      ],
      temperature=0.0,
      max_tokens=2500,
    )
    draft = AnnotationDraft.model_validate(response.content)

    if draft.issue_status != "present" and not is_revision:
      if was_excluded:
        return "excluded"  # stays excluded; already recorded
      exclude_example(
        example_path,
        example_id,
        f"issue not present at head ({draft.issue_status}): {draft.presence_explanation}",
      )
      return "excluded"

    gc["severity"] = draft.severity
    gc["evidence_requirement"] = draft.evidence_requirement
    gc["rationale"] = draft.rationale
    gc["must_not_claim"] = draft.must_not_claim
    changed = True

  if changed:
    example["human_label_notes"] = (
      f"pre-annotated by {model}; NEEDS HUMAN REVIEW — edit fields in place, then remove this stamp"
    )
    example_path.write_text(json.dumps(example, indent=2) + "\n")
    if was_excluded:
      restore_example(example_path, example)
      return "restored"
  return "drafted" if changed else "skipped"


async def main() -> None:
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("--split", default=None, help="development|validation|holdout")
  parser.add_argument("--limit", type=int, default=None, help="smoke-test N examples")
  parser.add_argument("--model", default=None, help="default: review model from settings")
  parser.add_argument("--force", action="store_true", help="re-draft even annotated fields")
  parser.add_argument(
    "--include-excluded",
    action="store_true",
    help="also re-judge _excluded/; 'present' verdicts auto-restore to their split",
  )
  args = parser.parse_args()

  settings = get_settings()
  model = args.model or settings.openrouter_review_model

  roots = [GOLDEN_TEXT_ROOT / args.split] if args.split else sorted(GOLDEN_TEXT_ROOT.iterdir())
  example_paths = sorted(
    p
    for root in roots
    if root.is_dir() and root.name != "_excluded"
    for p in root.glob("*/example.json")
  )
  if args.include_excluded and EXCLUDED_ROOT.is_dir():
    example_paths += sorted(EXCLUDED_ROOT.glob("*/example.json"))
  if args.limit:
    example_paths = example_paths[: args.limit]
  if not example_paths:
    raise SystemExit("No example.json files found")

  client = OpenRouterClient()
  sem = asyncio.Semaphore(CONCURRENCY)
  counts = {"drafted": 0, "skipped": 0, "excluded": 0, "restored": 0, "error": 0}

  async def guarded(path: Path) -> None:
    async with sem:
      try:
        outcome = await annotate_example(client, model, path, args.force)
      except Exception as exc:
        # One example's failure (truncated JSON, transport, anything) must
        # never kill the batch — record and continue.
        record_failure(path.parent.name, f"annotate: {type(exc).__name__}: {exc}")
        counts["error"] += 1
        print(f"error: {path.parent.name}")
        return
      counts[outcome] += 1
      print(f"{outcome}: {path.parent.name}")

  await asyncio.gather(*(guarded(p) for p in example_paths))
  await client.aclose()

  # Validation census over examples currently in the three split dirs.
  remaining = sorted(
    p
    for root in sorted(GOLDEN_TEXT_ROOT.iterdir())
    if root.is_dir() and root.name != "_excluded"
    for p in root.glob("*/example.json")
  )
  valid, invalid = 0, []
  for path in remaining:
    try:
      GoldenExample.model_validate(json.loads(path.read_text()))
      valid += 1
    except Exception as exc:
      invalid.append((path.parent.name, str(exc).split("\n")[1][:120]))

  print(
    f"\n{counts['drafted']} drafted, {counts['restored']} restored, "
    f"{counts['excluded']} excluded, {counts['skipped']} untouched, "
    f"{counts['error']} errors — model {model}."
  )
  print(f"GoldenExample-valid: {valid}/{len(remaining)}")
  if invalid:
    print("Still invalid:")
    for example_id, reason in invalid:
      print(f"  - {example_id}: {reason}")
  if counts["drafted"] or counts["restored"]:
    print(
      '\nReview pass: grep -rl "NEEDS HUMAN REVIEW" data/golden/text | '
      "then edit + remove the stamp."
    )


if __name__ == "__main__":
  asyncio.run(main())
