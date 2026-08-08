"""widen github_review_id to bigint

Revision ID: dfff9e42b411
Revises: 7f997b7c8b7a
Create Date: 2026-08-08 17:24:36.652951

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "dfff9e42b411"
down_revision: str | Sequence[str] | None = "7f997b7c8b7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "review_runs",
        "github_review_id",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        "review_runs",
        "github_review_id",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
