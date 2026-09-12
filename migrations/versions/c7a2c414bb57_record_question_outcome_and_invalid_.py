"""record question outcome and invalid citation count

Revision ID: c7a2c414bb57
Revises: d79265596fd5
Create Date: 2026-09-11 22:49:56.526648

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c7a2c414bb57"
down_revision: str | Sequence[str] | None = "d79265596fd5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with Question.__table_args__.
OUTCOMES = "outcome IN ('answered', 'insufficient_evidence', 'declined', 'failed')"


def upgrade() -> None:
    """Replace the insufficient-evidence flag with an outcome every question records.

    `outcome` is added NOT NULL with no default and no backfill: nothing has written a
    question yet, so the table is empty, and a default would let a caller that forgets
    the outcome record a question with an invented one.
    """
    op.add_column("questions", sa.Column("outcome", sa.String(length=30), nullable=False))
    op.create_check_constraint("ck_questions_outcome", "questions", OUTCOMES)
    op.add_column("questions", sa.Column("invalid_citations", sa.Integer(), nullable=True))
    op.add_column("questions", sa.Column("error_class", sa.String(length=100), nullable=True))
    op.drop_column("questions", "insufficient_evidence")


def downgrade() -> None:
    """Restore the flag, derived from each row's outcome, then drop the new columns."""
    op.add_column("questions", sa.Column("insufficient_evidence", sa.Boolean(), nullable=True))
    op.execute("UPDATE questions SET insufficient_evidence = (outcome = 'insufficient_evidence')")
    op.alter_column("questions", "insufficient_evidence", nullable=False)
    op.drop_column("questions", "error_class")
    op.drop_column("questions", "invalid_citations")
    op.drop_constraint("ck_questions_outcome", "questions", type_="check")
    op.drop_column("questions", "outcome")
