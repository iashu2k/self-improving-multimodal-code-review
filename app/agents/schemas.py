from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class Severity(StrEnum):
  CRITICAL = "critical"
  HIGH = "high"
  MEDIUM = "medium"
  LOW = "low"


class ReviewCategory(StrEnum):
  BUG_RISK = "bug_risk"
  SECURITY = "security"
  PERFORMANCE = "performance"
  MAINTAINABILITY = "maintainability"
  STYLE = "style"
  UI_REGRESSION = "ui_regression"


class ReviewComment(BaseModel):
  file_path: str = Field(description="Repo-relative path of the changed file")
  line: int = Field(description="Line number on the RIGHT (new) side of the diff")
  side: Literal["RIGHT"] = "RIGHT"
  severity: Severity
  category: ReviewCategory
  title: str = Field(max_length=120)
  body: str = Field(description="Claim, rationale, and impact; no speculation")
  evidence: list[str] = Field(
    min_length=1, description="Exact code lines or facts the claim rests on"
  )
  suggested_fix: str | None = None
  confidence: float = Field(ge=0.0, le=1.0)


class ReviewResult(BaseModel):
  summary: str = Field(description="One-paragraph PR-level summary")
  comments: list[ReviewComment]
  should_post_review: bool
  abstain_reason: str | None = None
