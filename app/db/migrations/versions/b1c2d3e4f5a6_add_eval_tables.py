"""add eval runs, example results, and matches tables

Revision ID: b1c2d3e4f5a6
Revises: dfff9e42b411
Create Date: 2026-08-12 15:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b1c2d3e4f5a6"
down_revision: str | None = "a37b78e1b225"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  op.create_table(
    "eval_runs",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("config_version", sa.String(64), nullable=False, index=True),
    sa.Column("dataset_split", sa.String(32), nullable=False),
    sa.Column("systems", postgresql.JSONB, nullable=False),
    sa.Column("status", sa.String(32), nullable=False, server_default="running"),
    sa.Column("aggregate_metrics", postgresql.JSONB, nullable=True),
    sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0"),
    sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
  )
  op.create_table(
    "eval_example_results",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
      "run_id",
      postgresql.UUID(as_uuid=True),
      sa.ForeignKey("eval_runs.id"),
      nullable=False,
      index=True,
    ),
    sa.Column("example_id", sa.String(128), nullable=False, index=True),
    sa.Column("system", sa.String(32), nullable=False, index=True),
    sa.Column("attempt", sa.Integer, nullable=False, server_default="1"),
    sa.Column("tp", sa.Integer, nullable=False, server_default="0"),
    sa.Column("fp", sa.Integer, nullable=False, server_default="0"),
    sa.Column("fn", sa.Integer, nullable=False, server_default="0"),
    sa.Column("precision", sa.Float, nullable=True),
    sa.Column("recall", sa.Float, nullable=True),
    sa.Column("f1", sa.Float, nullable=True),
    sa.Column("groundedness", sa.Float, nullable=True),
    sa.Column("line_validity", sa.Float, nullable=True),
    sa.Column("severity_agreement", sa.Float, nullable=True),
    sa.Column("expected_empty", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("predicted_empty", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
  )
  op.create_table(
    "eval_matches",
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column(
      "run_id",
      postgresql.UUID(as_uuid=True),
      sa.ForeignKey("eval_runs.id"),
      nullable=False,
      index=True,
    ),
    sa.Column(
      "example_result_id",
      postgresql.UUID(as_uuid=True),
      sa.ForeignKey("eval_example_results.id"),
      nullable=False,
    ),
    sa.Column("example_id", sa.String(128), nullable=False, index=True),
    sa.Column("gold_index", sa.Integer, nullable=False),
    sa.Column("generated_index", sa.Integer, nullable=True),
    sa.Column("verdict", sa.String(32), nullable=True),
    sa.Column("matched", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("judge_rationale", sa.Text, nullable=True),
    sa.Column("audited_by_human", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("human_agrees", sa.Boolean, nullable=True),
  )


def downgrade() -> None:
  op.drop_table("eval_matches")
  op.drop_table("eval_example_results")
  op.drop_table("eval_runs")
