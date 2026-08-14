"""Eval-run tables. Follows the Phase 3A conventions: async SQLAlchemy 2,
JSONB on Postgres with a JSON variant for SQLite tests, idempotent-friendly
natural keys, suppressions/matches persisted — not just aggregates.
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

JSONBVariant = JSON().with_variant(JSONB, "postgresql")


class EvalRun(Base):
  __tablename__ = "eval_runs"

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
  config_version: Mapped[str] = mapped_column(String(64), index=True)
  dataset_split: Mapped[str] = mapped_column(String(32))
  systems: Mapped[list] = mapped_column(JSONBVariant)
  status: Mapped[str] = mapped_column(String(32), default="running")
  aggregate_metrics: Mapped[list | None] = mapped_column(JSONBVariant, nullable=True)
  total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
  started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
  finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalExampleResult(Base):
  __tablename__ = "eval_example_results"

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
  run_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("eval_runs.id"), index=True
  )
  example_id: Mapped[str] = mapped_column(String(128), index=True)
  system: Mapped[str] = mapped_column(String(32), index=True)
  attempt: Mapped[int] = mapped_column(Integer, default=1)
  tp: Mapped[int] = mapped_column(Integer, default=0)
  fp: Mapped[int] = mapped_column(Integer, default=0)
  fn: Mapped[int] = mapped_column(Integer, default=0)
  precision: Mapped[float | None] = mapped_column(Float, nullable=True)
  recall: Mapped[float | None] = mapped_column(Float, nullable=True)
  f1: Mapped[float | None] = mapped_column(Float, nullable=True)
  groundedness: Mapped[float | None] = mapped_column(Float, nullable=True)
  line_validity: Mapped[float | None] = mapped_column(Float, nullable=True)
  severity_agreement: Mapped[float | None] = mapped_column(Float, nullable=True)
  expected_empty: Mapped[bool] = mapped_column(Boolean, default=False)
  predicted_empty: Mapped[bool] = mapped_column(Boolean, default=False)
  cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class EvalMatch(Base):
  """One judged gold/generated pair. Judge rationale is always stored so a
  human can audit a 20% sample — never trust LLM-as-judge alone."""

  __tablename__ = "eval_matches"

  id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
  run_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("eval_runs.id"), index=True
  )
  example_result_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("eval_example_results.id")
  )
  example_id: Mapped[str] = mapped_column(String(128), index=True)
  gold_index: Mapped[int] = mapped_column(Integer)
  generated_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
  verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
  matched: Mapped[bool] = mapped_column(Boolean, default=False)
  judge_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
  audited_by_human: Mapped[bool] = mapped_column(Boolean, default=False)
  human_agrees: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
