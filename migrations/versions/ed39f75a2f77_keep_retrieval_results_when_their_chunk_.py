"""keep retrieval results when their chunk is deleted

Revision ID: ed39f75a2f77
Revises: 99190e5e6d92
Create Date: 2026-09-12 21:31:34.952185

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ed39f75a2f77"
down_revision: str | Sequence[str] | None = "99190e5e6d92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# PostgreSQL's name for the key the initial migration created without naming it.
CHUNK_KEY = "retrieval_results_chunk_id_fkey"


def upgrade() -> None:
    """Keep a retrieval result when its chunk is deleted, as reindexing a document will.

    Under CASCADE, replacing a document's chunks would delete the retrieval results of every
    past question that retrieved them. SET NULL keeps each result's question, rank, score,
    and whether it was cited, losing only which chunk it was, so `chunk_id` becomes nullable.
    The unique constraint on (question_id, chunk_id) treats nulls as distinct, so one
    question can hold several such results.
    """
    op.drop_constraint(CHUNK_KEY, "retrieval_results", type_="foreignkey")
    op.alter_column("retrieval_results", "chunk_id", existing_type=sa.BigInteger(), nullable=True)
    op.create_foreign_key(
        CHUNK_KEY, "retrieval_results", "chunks", ["chunk_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    """Restore CASCADE and NOT NULL, first deleting results whose chunk is already gone.

    Those rows cannot satisfy NOT NULL, and under CASCADE their chunk's deletion would have
    deleted them anyway.
    """
    op.execute("DELETE FROM retrieval_results WHERE chunk_id IS NULL")
    op.drop_constraint(CHUNK_KEY, "retrieval_results", type_="foreignkey")
    op.alter_column("retrieval_results", "chunk_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_foreign_key(
        CHUNK_KEY, "retrieval_results", "chunks", ["chunk_id"], ["id"], ondelete="CASCADE"
    )
