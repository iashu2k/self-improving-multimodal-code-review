import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JSONBVariant = JSON().with_variant(JSONB(), "postgresql")


class ConfigurationStatus(StrEnum):
  DRAFT = "draft"
  PENDING = "pending"
  ACTIVE = "active"
  REJECTED = "rejected"
  ROLLED_BACK = "rolled_back"


class ReviewConfiguration(Base):
  __tablename__ = "review_configurations"
  __table_args__ = (
    Index(
      "ix_review_configurations_status_created",
      "status",
      "created_at",
    ),
  )

  id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid.uuid4,
  )
  config_version: Mapped[str] = mapped_column(
    String(64),
    unique=True,
    index=True,
  )
  parent_version: Mapped[str | None] = mapped_column(
    String(64),
    nullable=True,
  )
  change_reason: Mapped[str] = mapped_column(Text)
  status: Mapped[str] = mapped_column(
    String(32),
    default=ConfigurationStatus.DRAFT,
    index=True,
  )

  router_rules: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  generator_prompt_version: Mapped[str] = mapped_column(String(64))
  critic_prompt_version: Mapped[str] = mapped_column(String(64))
  thresholds: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  model_versions: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  retrieval_config: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  repair_policy: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  evaluation_summary: Mapped[dict] = mapped_column(JSONBVariant, default=dict)

  created_by: Mapped[str] = mapped_column(String(128), default="manual")
  created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(UTC),
  )
  evaluated_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
  )
  approved_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
  )
  approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
  promoted_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
  )
  rejected_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
  )
  rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
  rolled_back_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True),
    nullable=True,
  )
  rollback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConfigurationEvaluation(Base):
  __tablename__ = "configuration_evaluations"

  id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid.uuid4,
  )
  configuration_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True),
    ForeignKey("review_configurations.id", ondelete="CASCADE"),
    index=True,
  )
  dataset_split: Mapped[str] = mapped_column(String(32))
  system: Mapped[str] = mapped_column(String(64))
  repeat_number: Mapped[int]
  precision: Mapped[float | None] = mapped_column(Float, nullable=True)
  recall: Mapped[float | None] = mapped_column(Float, nullable=True)
  f1: Mapped[float | None] = mapped_column(Float, nullable=True)
  groundedness: Mapped[float | None] = mapped_column(Float, nullable=True)
  abstention_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
  no_comment_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
  safety_policy_failures: Mapped[int] = mapped_column(default=0)
  metrics: Mapped[dict] = mapped_column(JSONBVariant, default=dict)
  created_at: Mapped[datetime] = mapped_column(
    DateTime(timezone=True),
    default=lambda: datetime.now(UTC),
  )
