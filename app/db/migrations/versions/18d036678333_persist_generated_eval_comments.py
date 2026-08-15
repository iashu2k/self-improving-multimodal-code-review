"""persist_generated_eval_comments

Revision ID: 18d036678333
Revises: b1c2d3e4f5a6
Create Date: 2026-08-14 21:25:23.582119

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "18d036678333"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column(
    "eval_example_results",
    sa.Column("generated_comments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
  )


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_column("eval_example_results", "generated_comments")
