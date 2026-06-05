"""Rename preset sim bots to use their strategy profile name

Revision ID: 0020
Revises: 0019
Create Date: 2026-06-05

Preset sims were previously given a random historical name (e.g. a Greek god)
from the name pool on provisioning. Now each preset sim should be named after
its strategy profile (e.g. "Coalition Seeker"). This migration back-fills all
active (non-archived) preset sims in production.

Scope: bots where kind='sim', sim_profile_id IS NOT NULL,
       sim_profile_name IS NOT NULL, archived_at IS NULL.

Downgrade is a no-op — the old names were not stored and cannot be recovered.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            UPDATE bots
            SET name = sim_profile_name
            WHERE kind = 'sim'
              AND sim_profile_id IS NOT NULL
              AND sim_profile_name IS NOT NULL
              AND archived_at IS NULL
            """
        )
    )


def downgrade() -> None:
    pass
