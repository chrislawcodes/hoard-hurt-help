"""One-time cleanup: disable all providers on Mode A connections.

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-14

This pairs with the single-provider MCP change. Previously, signing in with
ANY MCP ("Mode A") client enabled ALL providers on the user's one Mode A
connection. Now each MCP connection enables only the one provider whose client
actually connected.

Existing Mode A connections in the database still have ALL providers enabled
from the old behavior. This migration disables every provider flag on Mode A
connections (rows in `connections` where `mode_a_at IS NOT NULL`), so each
provider re-enables correctly the next time its real client connects.

Machine/connector connections (mode_a_at IS NULL) are left untouched.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: Union[str, None] = "0034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Lightweight table/column descriptors so the UPDATE renders correctly on
    # both Postgres (prod) and SQLite (dev/test). Using SQLAlchemy core avoids
    # hand-writing dialect-specific boolean literals.
    connections = sa.table("connections", sa.column("id"), sa.column("mode_a_at"))
    connection_providers = sa.table(
        "connection_providers",
        sa.column("enabled", sa.Boolean),
        sa.column("connection_id"),
    )

    op.execute(
        connection_providers.update()
        .where(
            connection_providers.c.connection_id.in_(
                sa.select(connections.c.id).where(
                    connections.c.mode_a_at.is_not(None)
                )
            )
        )
        .values(enabled=False)
    )


def downgrade() -> None:
    # No-op: this is a one-time data cleanup that is not meaningfully
    # reversible. We cannot reconstruct which providers were truly connected,
    # so re-enabling them would invent data. Intentionally left as a no-op.
    pass
