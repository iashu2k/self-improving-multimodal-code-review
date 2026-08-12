# app/eval/golden_schemas.py
from enum import StrEnum

from pydantic import BaseModel, model_validator

# reuse, don't redefine
from app.agents.schemas import ReviewCategory as Category
from app.agents.schemas import Severity


class ExpectedOutcome(StrEnum):
  COMMENT_EXPECTED = "comment_expected"
  NO_COMMENT = "no_comment"


class Split(StrEnum):
  DEVELOPMENT = "development"
  VALIDATION = "validation"
  HOLDOUT = "holdout"


class GoldComment(BaseModel):
  file_path: str
  line: int  # RIGHT-side line in the real fetched diff
  category: Category
  severity: Severity
  issue_summary: str
  evidence_requirement: str  # what the agent must cite to get credit
  # overclaim tripwires (repair-sycophancy probes)
  must_not_claim: list[str] = []
  requires_repo_context: bool = False
  requires_screenshot: bool = False
  rationale: str  # why this gold issue is valid


class GoldenExample(BaseModel):
  example_id: str  # "pr_0042"
  # "github-codereview" | "review-sandbox" | "phase1-fixture"
  source: str
  repository: str  # "owner/repo"
  commit_sha: str
  language: str
  pr_metadata: dict  # title, description, url
  changed_files: list[str]
  diff_path: str  # relative: "development/pr_0042/diff.patch"
  expected_outcome: ExpectedOutcome
  gold_comments: list[GoldComment] = []
  context_files: list[str] = []
  no_comment_rationale: str = ""
  human_label_notes: str = ""
  split: Split | None = None

  @model_validator(mode="after")
  def check_consistency(self):
    if self.expected_outcome == ExpectedOutcome.NO_COMMENT:
      if self.gold_comments:
        raise ValueError("no_comment examples must have empty gold_comments")
      if not self.no_comment_rationale:
        raise ValueError("no_comment examples must document why silence is correct")
    else:
      if not self.gold_comments:
        raise ValueError("comment_expected examples need >=1 gold comment")
    return self
