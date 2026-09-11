"""add partial index for job claims

Revision ID: f8ace89d692d
Revises: f8ca8c53f0d3
Create Date: 2026-09-11 14:55:21.914821

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8ace89d692d"
down_revision: str | Sequence[str] | None = "f8ca8c53f0d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in step with IngestionJob.__table_args__ and with the claim and expiry queries in
# app/db/jobs.py, whose conditions must imply this one for Postgres to use the index.
CLAIMABLE = "status IN ('queued', 'running')"


def upgrade() -> None:
    """Index the jobs a worker can still act on, for the claim and expiry queries.

    Partial, so it holds only queued and running rows while finished jobs accumulate
    without bound. On a million-row test table it took the claim from 44.6 ms, a full
    scan and sort, to 0.011 ms.
    """
    op.create_index(
        "ix_ingestion_jobs_claimable",
        "ingestion_jobs",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text(CLAIMABLE),
    )


def downgrade() -> None:
    """Drop the index; the claim goes back to scanning every job."""
    op.drop_index(
        "ix_ingestion_jobs_claimable",
        table_name="ingestion_jobs",
        postgresql_where=sa.text(CLAIMABLE),
    )
