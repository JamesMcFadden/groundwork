"""add hnsw index for filtered vector search

Revision ID: d79265596fd5
Revises: f8ace89d692d
Create Date: 2026-09-11 22:34:32.257374

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d79265596fd5"
down_revision: str | Sequence[str] | None = "f8ace89d692d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Index chunk embeddings for approximate nearest-neighbour search.

    HNSW with pgvector's defaults, m 16 and ef_construction 64. `vector_ip_ops` serves
    the inner-product ordering search uses; with any other operator class the index would
    sit unused. The build blocks writes to `chunks` until it finishes, which is brief at
    this table's size; over a large table it would be built concurrently instead.
    """
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_ip_ops"},
    )


def downgrade() -> None:
    """Drop the index; search goes back to comparing the question with every chunk."""
    op.drop_index(
        "ix_chunks_embedding_hnsw",
        table_name="chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_ip_ops"},
    )
