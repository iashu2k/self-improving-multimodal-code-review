from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, BigInteger, DateTime, Float, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class RunStatus(StrEnum):
  RUNNING = "running"
  ABSTAINED = "abstained"
  PUBLISHED = "published"
  FAILED = "failed"


class CommentStatus(StrEnum):
  POSTED = "posted"
  SUPPRESSED = "suppressed"


class ReviewRun(Base):
  __tablename__ = "review_runs"
  __table_args__ = (
    UniqueConstraint(
      "repo_owner",
      "repo_name",
      "pr_number",
      "head_sha",
      "config_version",
      name="uq_review_run_identity",
    ),
  )

  id: Mapped[int] = mapped_column(primary_key=True)
  repo_owner: Mapped[str] = mapped_column(String(255))
  repo_name: Mapped[str] = mapped_column(String(255))
  pr_number: Mapped[int]
  head_sha: Mapped[str] = mapped_column(String(64))
  config_version: Mapped[str] = mapped_column(String(50))
  status: Mapped[str] = mapped_column(String(20), default=RunStatus.RUNNING, index=True)
  abstain_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
  error: Mapped[str | None] = mapped_column(Text, nullable=True)
  github_review_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
  created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), default=lambda: datetime.now(UTC)
  )
  completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

  comments: Mapped[list["StoredReviewComment"]] = relationship(
    back_populates="run", cascade="all, delete-orphan"
  )


class StoredReviewComment(Base):
  __tablename__ = "review_comments"

  id: Mapped[int] = mapped_column(primary_key=True)
  run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id"), index=True)
  file_path: Mapped[str] = mapped_column(String(500))
  line: Mapped[int]
  severity: Mapped[str] = mapped_column(String(20))
  category: Mapped[str] = mapped_column(String(30))
  title: Mapped[str] = mapped_column(String(200))
  body: Mapped[str] = mapped_column(Text)
  suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
  confidence: Mapped[float] = mapped_column(Float)
  status: Mapped[str] = mapped_column(String(20))
  suppression_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

  run: Mapped[ReviewRun] = relationship(back_populates="comments")


class ReviewRunEvent(Base):
  __tablename__ = "review_run_events"

  id: Mapped[int] = mapped_column(primary_key=True)
  run_id: Mapped[int] = mapped_column(ForeignKey("review_runs.id", ondelete="CASCADE"), index=True)
  node: Mapped[str] = mapped_column(String(32))
  detail: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"), default=dict)
  created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True), default=lambda: datetime.now(UTC)
  )
