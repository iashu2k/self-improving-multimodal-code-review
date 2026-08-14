"""LLM judge: semantic equivalence + groundedness, with persisted rationale.

The judge never replaces the deterministic layers — it only ranks pairs that
already share file and category equivalence. Every call
returns rationales which are stored verbatim for the 20% human audit.

Field contracts (verified):
  GoldComment: file_path, line, category, severity, issue_summary,
               evidence_requirement, must_not_claim, rationale
  ReviewComment: file_path, line, category, severity, title, body, evidence
"""

import json
import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, Field

from app.agents.schemas import ReviewComment
from app.evals.schemas import CandidatePair, JudgeDecision, JudgeResponse

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _hunks(diff_text: str):
  """Yield (path, new_start, new_end, text) for each hunk."""
  path, lines, i = None, diff_text.splitlines(keepends=True), 0
  while i < len(lines):
    if lines[i].startswith("+++ b/"):
      path = lines[i][6:].strip()
      i += 1
      continue
    m = _HUNK_RE.match(lines[i])
    if m and path:
      start, count = int(m.group(1)), int(m.group(2) or 1)
      j = i
      while j + 1 < len(lines) and not lines[j + 1].startswith(("@@", "diff --git")):
        j += 1
      yield path, start, start + count, "".join(lines[i : j + 1])
      i = j + 1
      continue
    i += 1


def _excerpt(diff_text: str, targets: set[tuple[str, int]], pad: int = 30, cap: int = 12000) -> str:
  """Hunks overlapping the (path, line) targets; head-truncation fallback."""
  if targets:
    picked = [
      text
      for path, start, end, text in _hunks(diff_text)
      if any(path == f and start - pad <= line <= end + pad for f, line in targets)
    ]
    if picked:
      return "".join(picked)[:cap]
  return diff_text[:cap]


class JudgeClient(Protocol):
  async def judge(self, system: str, user: str, schema_name: str, json_schema: dict) -> str: ...


class GroundednessVerdict(BaseModel):
  grounded: bool
  rationale: str = Field(min_length=1)


class GroundednessResponse(BaseModel):
  verdicts: list[GroundednessVerdict]


EQUIVALENCE_SYSTEM = """You are judging whether a generated code-review comment describes the same
underlying issue as a gold (human-written) review comment on the same pull request.

Verdicts:
- equivalent: same root cause and same actionable concern, even if wording/severity differ.
- related_but_distinct: same area, but a materially different issue or fix.
- different: unrelated, incorrect, or unsupported by the shown diff/context.

Reply with JSON: {"decisions": [{"gold_index", "generated_index", "verdict", "rationale"}]}.
One decision per input pair. Rationale is mandatory and is stored for human audit."""

GROUNDEDNESS_SYSTEM = """For each generated review comment, decide
whether every
factual claim it makes is supported by the provided diff hunk or
retrieved repository context.
Unsupported numbers, invented identifiers, or claims about code not
shown are ungrounded.

Reply with JSON: {"verdicts": [{"grounded": true|false, "rationale":
"..."}]} in input order."""


def _strict(schema):
  """OpenAI strict mode requires additionalProperties:false on every object."""
  if isinstance(schema, dict):
    if schema.get("type") == "object":
      schema["additionalProperties"] = False
    for value in schema.values():
      _strict(value)
  elif isinstance(schema, list):
    for item in schema:
      _strict(item)
  return schema


def _render_pair(
  pair: CandidatePair,
  gold_comments: Sequence[object],
  generated: Sequence[ReviewComment],
) -> dict[str, object]:
  gold = gold_comments[pair.gold_index]
  pred = generated[pair.generated_index]
  return {
    "gold_index": pair.gold_index,
    "generated_index": pair.generated_index,
    "gold": {
      "file": gold.file_path,
      "line": gold.line,
      "category": str(gold.category),
      "severity": str(gold.severity),
      "issue_summary": gold.issue_summary,
      "evidence_requirement": gold.evidence_requirement,
      "must_not_claim": gold.must_not_claim,
    },
    "generated": {
      "file": pred.file_path,
      "line": pred.line,
      "category": str(pred.category),
      "severity": str(pred.severity),
      "title": pred.title,
      "body": pred.body,
      "evidence": pred.evidence,
    },
  }


async def judge_equivalence(
  client: JudgeClient,
  example_id: str,
  pairs: Sequence[CandidatePair],
  gold_comments: Sequence[object],
  generated: Sequence[ReviewComment],
  diff_text: str,
) -> list[JudgeDecision]:
  if not pairs:
    return []
  targets = {
    (gold_comments[p.gold_index].file_path, gold_comments[p.gold_index].line) for p in pairs
  } | {(generated[p.generated_index].file_path, generated[p.generated_index].line) for p in pairs}
  user = json.dumps(
    {
      "example_id": example_id,
      "diff_excerpt": _excerpt(diff_text, targets),
      "pairs": [_render_pair(p, gold_comments, generated) for p in pairs],
    }
  )
  raw = await client.judge(
    EQUIVALENCE_SYSTEM, user, "JudgeResponse", _strict(JudgeResponse.model_json_schema())
  )
  return JudgeResponse.model_validate_json(raw).decisions


async def judge_groundedness(
  client: JudgeClient,
  generated: Sequence[ReviewComment],
  diff_text: str,
  retrieved_context: str,
) -> list[GroundednessVerdict]:
  if not generated:
    return []
  user = json.dumps(
    {
      "diff_excerpt": _excerpt(diff_text, {(c.file_path, c.line) for c in generated}),
      "retrieved_context": retrieved_context[:4000],
      "comments": [{"title": c.title, "body": c.body, "evidence": c.evidence} for c in generated],
    }
  )
  raw = await client.judge(
    GROUNDEDNESS_SYSTEM,
    user,
    "GroundednessResponse",
    _strict(GroundednessResponse.model_json_schema()),
  )
  return GroundednessResponse.model_validate_json(raw).verdicts
