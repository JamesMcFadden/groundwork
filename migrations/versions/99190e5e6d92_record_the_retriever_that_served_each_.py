"""record the retriever that served each question

Revision ID: 99190e5e6d92
Revises: b0fd12a0a380
Create Date: 2026-09-12 15:11:49.883625

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "99190e5e6d92"
down_revision: str | Sequence[str] | None = "b0fd12a0a380"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with Question.__table_args__.
RETRIEVERS = "retriever IN ('dense', 'hybrid')"


def upgrade() -> None:
    """Record which search retrieved each question's chunks.

    Existing rows are backfilled as `dense`, the only search the service has run, before the
    column becomes NOT NULL. No default is left behind: a default would let a caller that
    forgets the retriever record one that never ran.
    """
    op.add_column("questions", sa.Column("retriever", sa.String(length=20), nullable=True))
    op.execute("UPDATE questions SET retriever = 'dense'")
    op.alter_column("questions", "retriever", nullable=False)
    op.create_check_constraint("ck_questions_retriever", "questions", RETRIEVERS)


def downgrade() -> None:
    """Drop the constraint and the column."""
    op.drop_constraint("ck_questions_retriever", "questions", type_="check")
    op.drop_column("questions", "retriever")
