import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FeedbackLabel(StrEnum):
  HELPFUL = "helpful"
  FALSE_POSITIVE = "false_positive"
  WRONG_SEVERITY = "wrong_severity"
  NOT_ACTIONABLE = "not_actionable"
  MISSING_CONTEXT = "missing_context"
  DUPLICATE = "duplicate"


class FeedbackActorType(StrEnum):
  DEVELOPER = "developer"
  MAINTAINER = "maintainer"
  ADMIN = "admin"


class FeedbackSource(StrEnum):
  GITHUB_REACTION = "github_reaction"
  GITHUB_COMMENT_COMMAND = "github_comment_command"
  DASHBOARD = "dashboard"
  MANUAL_REVIEW = "manual_review"


class FeedbackTargetType(StrEnum):
  COMMENT = "comment"
  REVIEW_SUMMARY = "review_summary"


class AttributionConfidence(StrEnum):
  EXACT_MARKER = "exact_marker"
  MARKER_COMMENT_MATCH = "marker_comment_match"
  MANUAL = "manual"


class CommentFeedback(Base):
  __tablename__ = "comment_feedback"
  __table_args__ = (
    UniqueConstraint(
      "source",
      "source_event_id",
      name="uq_comment_feedback_source_event",
    ),
    Index("ix_comment_feedback_run_created", "run_id", "created_at"),
    Index("ix_comment_feedback_label_created", "label", "created_at"),
  )

  id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid.uuid4,
  )
  run_id: Mapped[int] = mapped_column(
    ForeignKey("review_runs.id", ondelete="CASCADE"),
    index=True,
  )
  stored_comment_id: Mapped[int | None] = mapped_column(
    ForeignKey("review_comments.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
  )
  target_type: Mapped[str] = mapped_column(String(32))
  label: Mapped[str] = mapped_column(String(32), index=True)
  free_text: Mapped[str | None] = mapped_column(Text, nullable=True)
  actor_type: Mapped[str] = mapped_column(String(32))
  actor_login_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
  source: Mapped[str] = mapped_column(String(32))
  source_event_id: Mapped[str] = mapped_column(String(128))
  source_artifact_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
  attribution_confidence: Mapped[str] = mapped_column(String(32))
  created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  recorded_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(UTC),
  )
