"""add_comment_feedback

Revision ID: 3f1a2b4c5d6e
Revises: 18d036678333
Create Date: 2026-08-15 15:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "3f1a2b4c5d6e"
down_revision: str | Sequence[str] | None = "18d036678333"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "comment_feedback",
    sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("run_id", sa.Integer(), nullable=False),
    sa.Column("stored_comment_id", sa.Integer(), nullable=True),
    sa.Column("target_type", sa.String(length=32), nullable=False),
    sa.Column("label", sa.String(length=32), nullable=False),
    sa.Column("free_text", sa.Text(), nullable=True),
    sa.Column("actor_type", sa.String(length=32), nullable=False),
    sa.Column("actor_login_hash", sa.String(length=64), nullable=True),
    sa.Column("source", sa.String(length=32), nullable=False),
    sa.Column("source_event_id", sa.String(length=128), nullable=False),
    sa.Column("source_artifact_id", sa.String(length=128), nullable=True),
    sa.Column("attribution_confidence", sa.String(length=32), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
      ["run_id"],
      ["review_runs.id"],
      name="fk_comment_feedback_run_id_review_runs",
      ondelete="CASCADE",
    ),
    sa.ForeignKeyConstraint(
      ["stored_comment_id"],
      ["review_comments.id"],
      name="fk_comment_feedback_stored_comment_id_review_comments",
      ondelete="SET NULL",
    ),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint(
      "source",
      "source_event_id",
      name="uq_comment_feedback_source_event",
    ),
  )
  op.create_index(
    "ix_comment_feedback_run_id",
    "comment_feedback",
    ["run_id"],
    unique=False,
  )
  op.create_index(
    "ix_comment_feedback_stored_comment_id",
    "comment_feedback",
    ["stored_comment_id"],
    unique=False,
  )
  op.create_index(
    "ix_comment_feedback_label",
    "comment_feedback",
    ["label"],
    unique=False,
  )
  op.create_index(
    "ix_comment_feedback_run_created",
    "comment_feedback",
    ["run_id", "created_at"],
    unique=False,
  )
  op.create_index(
    "ix_comment_feedback_label_created",
    "comment_feedback",
    ["label", "created_at"],
    unique=False,
  )


def downgrade() -> None:
  op.drop_index("ix_comment_feedback_label_created", table_name="comment_feedback")
  op.drop_index("ix_comment_feedback_run_created", table_name="comment_feedback")
  op.drop_index("ix_comment_feedback_label", table_name="comment_feedback")
  op.drop_index("ix_comment_feedback_stored_comment_id", table_name="comment_feedback")
  op.drop_index("ix_comment_feedback_run_id", table_name="comment_feedback")
  op.drop_table("comment_feedback")
