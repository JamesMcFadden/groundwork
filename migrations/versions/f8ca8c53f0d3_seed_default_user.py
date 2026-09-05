"""seed default user

Revision ID: f8ca8c53f0d3
Revises: 9f34c3d0a0c5
Create Date: 2026-09-05 09:11:20.876175

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f8ca8c53f0d3"
down_revision: str | Sequence[str] | None = "9f34c3d0a0c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Kept in sync with Settings.default_user_id.
DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_USER_EMAIL = "dev@groundwork.local"


def upgrade() -> None:
    """Seed the user that owns all data until authentication exists.

    Data lives in a migration rather than a separate seed script so every environment —
    local, CI, RDS — is reproducible from `alembic upgrade head` alone.
    """
    op.execute(
        f"INSERT INTO users (id, email, created_at) "
        f"VALUES ('{DEFAULT_USER_ID}'::uuid, '{DEFAULT_USER_EMAIL}', now()) "
        f"ON CONFLICT (id) DO NOTHING"
    )


def downgrade() -> None:
    """Remove the seeded user. Cascades to anything it owns."""
    op.execute(f"DELETE FROM users WHERE id = '{DEFAULT_USER_ID}'::uuid")
