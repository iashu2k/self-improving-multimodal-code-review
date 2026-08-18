"""add_review_configurations

Revision ID: 7a4b9c1d2e3f
Revises: 3f1a2b4c5d6e
Create Date: 2026-08-18 18:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "7a4b9c1d2e3f"
down_revision: str | Sequence[str] | None = "3f1a2b4c5d6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "review_configurations",
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("config_version", sa.String(length=64), nullable=False),
    sa.Column("parent_version", sa.String(length=64), nullable=True),
    sa.Column("change_reason", sa.Text(), nullable=False),
    sa.Column("status", sa.String(length=32), nullable=False),
    sa.Column("router_rules", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("generator_prompt_version", sa.String(length=64), nullable=False),
    sa.Column("critic_prompt_version", sa.String(length=64), nullable=False),
    sa.Column("thresholds", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("model_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("retrieval_config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("repair_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("evaluation_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("created_by", sa.String(length=128), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("approved_by", sa.String(length=128), nullable=True),
    sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("rejection_reason", sa.Text(), nullable=True),
    sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("rollback_reason", sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("config_version", name="uq_review_configurations_version"),
  )
  op.create_index(
    "ix_review_configurations_config_version",
    "review_configurations",
    ["config_version"],
    unique=False,
  )
  op.create_index(
    "ix_review_configurations_status",
    "review_configurations",
    ["status"],
    unique=False,
  )
  op.create_index(
    "ix_review_configurations_status_created",
    "review_configurations",
    ["status", "created_at"],
    unique=False,
  )

  op.create_table(
    "configuration_evaluations",
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("configuration_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("dataset_split", sa.String(length=32), nullable=False),
    sa.Column("system", sa.String(length=64), nullable=False),
    sa.Column("repeat_number", sa.Integer(), nullable=False),
    sa.Column("precision", sa.Float(), nullable=True),
    sa.Column("recall", sa.Float(), nullable=True),
    sa.Column("f1", sa.Float(), nullable=True),
    sa.Column("groundedness", sa.Float(), nullable=True),
    sa.Column("abstention_accuracy", sa.Float(), nullable=True),
    sa.Column("no_comment_accuracy", sa.Float(), nullable=True),
    sa.Column("safety_policy_failures", sa.Integer(), nullable=False),
    sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
      ["configuration_id"],
      ["review_configurations.id"],
      name="fk_configuration_evaluations_configuration_id",
      ondelete="CASCADE",
    ),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(
    "ix_configuration_evaluations_configuration_id",
    "configuration_evaluations",
    ["configuration_id"],
    unique=False,
  )


def downgrade() -> None:
  op.drop_index(
    "ix_configuration_evaluations_configuration_id",
    table_name="configuration_evaluations",
  )
  op.drop_table("configuration_evaluations")

  op.drop_index(
    "ix_review_configurations_status_created",
    table_name="review_configurations",
  )
  op.drop_index("ix_review_configurations_status", table_name="review_configurations")
  op.drop_index(
    "ix_review_configurations_config_version",
    table_name="review_configurations",
  )
  op.drop_table("review_configurations")
