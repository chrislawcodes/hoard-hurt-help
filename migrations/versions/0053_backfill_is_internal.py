"""flag existing platform-owned accounts as internal

Without this, the engagement dashboard mostly measures our own testing. In the
dev database the house, sim and harness accounts own more than 500 of 646 player
rows, and all but one of them run agents marked as real AI — so filtering on
agent kind does not touch them.

The domain list is written out here rather than read from settings on purpose. A
migration has to produce the same result whenever it runs; one that changes
behaviour with an environment variable is not reproducible, and re-running it
after a config change would silently reclassify accounts.

This is a one-way data change in practice: downgrade drops the column, so the
flags are gone rather than restored. Rehearse it with
``scripts/preview_internal_backfill.py --db copy.db --dry-run`` before merging —
merging deploys, and railway.json runs migrations as a preDeployCommand.

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0053"
down_revision: Union[str, None] = "0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen at the time of this migration. Keep in step with
# Settings.internal_email_domains for NEW accounts, but do not read it from there.
INTERNAL_DOMAINS = ("agentludum.local", "house.local", "local.test")

# The platform's own bots account, which owns every scripted bot seat.
BOTS_USER_SUB = "platform:bots"


def upgrade() -> None:
    users = sa.table(
        "users",
        sa.column("email", sa.String),
        sa.column("google_sub", sa.String),
        sa.column("role", sa.String),
        sa.column("is_internal", sa.Boolean),
    )

    domain_match = sa.or_(
        *[
            sa.func.lower(users.c.email).like(f"%@{domain}")
            for domain in INTERNAL_DOMAINS
        ]
    )
    op.execute(
        users.update()
        .where(
            sa.or_(
                domain_match,
                users.c.google_sub == BOTS_USER_SUB,
                # Platform admins are us. Their role can change later, which is
                # exactly why the flag is stored now rather than derived on read.
                sa.func.lower(users.c.role) == "admin",
            )
        )
        .values(is_internal=True)
    )


def downgrade() -> None:
    # There is no "unflag" that restores prior state, because prior state was
    # "column does not exist". Migration 0052's downgrade drops it entirely.
    op.execute(
        sa.table("users", sa.column("is_internal", sa.Boolean))
        .update()
        .values(is_internal=False)
    )
