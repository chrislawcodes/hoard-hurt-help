"""Make MCP (Mode A) connections one-per-provider.

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-15

An MCP client speaks for exactly one AI provider (one client == one provider,
#392). The legacy model kept a single Mode A connection per user that accumulated
providers; the new model gives each provider its own connection, named by that
provider.

This migration:

1. Makes each live Mode A connection single-provider: it sets `connections.provider`
   (previously NULL on Mode A rows) to the one enabled provider, and — defensively,
   for any legacy row that still has several providers enabled — keeps the first and
   disables the rest. Those extra providers are NOT lost: the next time their real
   client connects, the app creates a fresh per-provider connection for them.
2. Replaces the "one live Mode A connection per user" unique index with
   "one live Mode A connection per (user, provider)".

Machine/connector connections (mode_a_at IS NULL) are untouched.

Prod note: production currently has exactly one Mode A connection with one provider
(claude), so step 1 just stamps its provider and the split branch never fires.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: Union[str, None] = "0035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    connections = sa.table(
        "connections",
        sa.column("id", sa.Integer),
        sa.column("provider", sa.String),
        sa.column("mode_a_at"),
        sa.column("deleted_at"),
    )
    connection_providers = sa.table(
        "connection_providers",
        sa.column("connection_id", sa.Integer),
        sa.column("provider", sa.String),
        sa.column("enabled", sa.Boolean),
    )

    # 1. One provider per live Mode A connection.
    mode_a_ids = list(
        bind.execute(
            sa.select(connections.c.id).where(
                connections.c.mode_a_at.is_not(None),
                connections.c.deleted_at.is_(None),
            )
        ).scalars()
    )
    for conn_id in mode_a_ids:
        enabled = list(
            bind.execute(
                sa.select(connection_providers.c.provider)
                .where(
                    connection_providers.c.connection_id == conn_id,
                    connection_providers.c.enabled.is_(True),
                )
                .order_by(connection_providers.c.provider)
            ).scalars()
        )
        if not enabled:
            # Signed in but no client has connected yet — leave provider NULL; it
            # is stamped when a real client connects.
            continue
        keep = enabled[0]
        bind.execute(
            connections.update()
            .where(connections.c.id == conn_id)
            .values(provider=keep)
        )
        if len(enabled) > 1:
            bind.execute(
                connection_providers.update()
                .where(
                    connection_providers.c.connection_id == conn_id,
                    connection_providers.c.provider != keep,
                    connection_providers.c.enabled.is_(True),
                )
                .values(enabled=False)
            )

    # 2. Swap the uniqueness rule to one live Mode A connection per (user, provider).
    op.drop_index("uq_connections_mode_a_user_id_live", table_name="connections")
    op.create_index(
        "uq_connections_mode_a_user_id_live",
        "connections",
        ["user_id", "provider"],
        unique=True,
        sqlite_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
    )


def downgrade() -> None:
    # Restore the one-per-user index. Provider stamps on Mode A rows are left in
    # place — harmless, and the legacy code path clears them on the next connect.
    op.drop_index("uq_connections_mode_a_user_id_live", table_name="connections")
    op.create_index(
        "uq_connections_mode_a_user_id_live",
        "connections",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
        postgresql_where=sa.text("mode_a_at IS NOT NULL AND deleted_at IS NULL"),
    )
