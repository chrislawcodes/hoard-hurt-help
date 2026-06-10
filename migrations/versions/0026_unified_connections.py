"""Foundation schema for unified connections.

Revision ID: 0026
Revises: 0025
Create Date: 2026-06-10
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.config import PROVIDER_MODELS

revision: str = "0026"
down_revision: Union[str, None] = "0025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _provider_for_model(model: str) -> str | None:
    for provider, models in PROVIDER_MODELS.items():
        if models and model in models:
            return provider
    return None


def upgrade() -> None:
    op.create_table(
        "connection_providers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("detected_detail", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name="fk_connection_providers_connection_id_connections",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "connection_id",
            "provider",
            name="uq_connection_providers_connection_id_provider",
        ),
    )
    op.create_index(
        "ix_connection_providers_connection_id",
        "connection_providers",
        ["connection_id"],
    )
    op.create_index(
        "ix_connection_providers_provider",
        "connection_providers",
        ["provider"],
    )

    conn = op.get_bind()
    connections = conn.execute(sa.text("SELECT id, provider FROM connections")).all()
    for connection_id, provider in connections:
        if provider is None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO connection_providers "
                "(connection_id, provider, enabled, detected, detected_detail, updated_at) "
                "VALUES (:connection_id, :provider, :enabled, :detected, NULL, CURRENT_TIMESTAMP)"
            ),
            {
                "connection_id": connection_id,
                "provider": provider,
                "enabled": True,
                "detected": False,
            },
        )

    with op.batch_alter_table("connections") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=16),
            nullable=True,
        )

    with op.batch_alter_table("connection_setups") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=16),
            nullable=True,
        )

    op.add_column(
        "agents",
        sa.Column("provider", sa.String(length=16), nullable=True),
    )
    op.create_index("ix_agents_provider", "agents", ["provider"])

    agent_rows = conn.execute(
        sa.text(
            """
            SELECT
                a.id AS agent_id,
                a.kind AS kind,
                a.connection_id AS connection_id,
                a.current_version_id AS current_version_id,
                c.provider AS connection_provider,
                v.model AS version_model
            FROM agents AS a
            LEFT JOIN connections AS c ON c.id = a.connection_id
            LEFT JOIN agent_versions AS v ON v.id = a.current_version_id
            ORDER BY a.id
            """
        )
    ).all()
    unresolved: list[int] = []
    for agent_id, kind, connection_id, current_version_id, connection_provider, version_model in agent_rows:
        if kind == "bot":
            continue
        provider: str | None = None
        if connection_id is not None and connection_provider is not None:
            provider = connection_provider
        elif current_version_id is not None and version_model is not None:
            provider = _provider_for_model(version_model)
        if provider is None:
            unresolved.append(agent_id)
            continue
        conn.execute(
            sa.text("UPDATE agents SET provider = :provider WHERE id = :agent_id"),
            {"provider": provider, "agent_id": agent_id},
        )
    if unresolved:
        raise RuntimeError(
            "Cannot backfill agents.provider for agent ids: "
            + ", ".join(str(agent_id) for agent_id in unresolved)
        )

    op.add_column(
        "players",
        sa.Column("served_by_connection_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "players",
        sa.Column("served_pinned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_players_served_by_connection_id",
        "players",
        ["served_by_connection_id"],
    )

    conn.execute(
        sa.text(
            """
            UPDATE players
            SET served_by_connection_id = (
                    SELECT a.connection_id
                    FROM agents AS a
                    WHERE a.id = players.agent_id
                ),
                served_pinned_at = CURRENT_TIMESTAMP
            WHERE match_id IN (
                SELECT id FROM matches WHERE state = 'active'
            )
              AND EXISTS (
                    SELECT 1
                    FROM agents AS a
                    WHERE a.id = players.agent_id
                      AND a.connection_id IS NOT NULL
                )
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_players_served_by_connection_id", table_name="players")
    op.drop_column("players", "served_pinned_at")
    op.drop_column("players", "served_by_connection_id")

    op.drop_index("ix_agents_provider", table_name="agents")
    op.drop_column("agents", "provider")

    with op.batch_alter_table("connection_setups") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=16),
            nullable=False,
        )

    with op.batch_alter_table("connections") as batch_op:
        batch_op.alter_column(
            "provider",
            existing_type=sa.String(length=16),
            nullable=False,
        )

    op.drop_index("ix_connection_providers_provider", table_name="connection_providers")
    op.drop_index("ix_connection_providers_connection_id", table_name="connection_providers")
    op.drop_table("connection_providers")
