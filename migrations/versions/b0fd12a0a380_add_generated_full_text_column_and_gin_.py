"""add generated full-text column and gin index to chunks

Revision ID: b0fd12a0a380
Revises: c7a2c414bb57
Create Date: 2026-09-12 14:39:03.227844

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b0fd12a0a380"
down_revision: str | Sequence[str] | None = "c7a2c414bb57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with Chunk.tsv. Naming the configuration makes to_tsvector immutable, as a
# generated column requires.
TSV = "to_tsvector('english', text)"


def upgrade() -> None:
    """Give every chunk full-text lexemes the database computes from its text.

    A stored generated column, so every path that writes chunks fills it: the worker, and
    the eval harness, which writes chunks directly. Adding it rewrites `chunks`, computing
    the column for every existing row, and the GIN index is then built over them. Both
    block writes to the table while they run, which is brief at this table's size.
    """
    op.add_column(
        "chunks",
        sa.Column("tsv", postgresql.TSVECTOR(), sa.Computed(TSV, persisted=True), nullable=False),
    )
    op.create_index("ix_chunks_tsv_gin", "chunks", ["tsv"], unique=False, postgresql_using="gin")


def downgrade() -> None:
    """Drop the index and the column; chunks keep their text, so nothing is lost."""
    op.drop_index("ix_chunks_tsv_gin", table_name="chunks", postgresql_using="gin")
    op.drop_column("chunks", "tsv")
