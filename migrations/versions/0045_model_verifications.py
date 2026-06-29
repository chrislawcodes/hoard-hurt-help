"""add model_verifications table for per-(connection, provider, model) status

The connector verifies whether its CLI login can run a given model and reports
the outcome here; the website surfaces it and the join flow warns on it. Keyed by
(connection, provider, model) — a new table because the connection_providers row
is unique per (connection, provider) and can't hold multiple models.

Revision ID: 0045
Revises: 0044
Create Date: 2026-06-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: Union[str, None] = "0044"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "model_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_text", sa.String(length=300), nullable=True),
        sa.Column(
            "consecutive_timeouts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connection_id"],
            ["connections.id"],
            name="fk_model_verifications_connection_id_connections",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "connection_id",
            "provider",
            "model",
            name="uq_model_verifications_conn_provider_model",
        ),
    )
    op.create_index(
        "ix_model_verifications_connection_id",
        "model_verifications",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_verifications_connection_id", table_name="model_verifications"
    )
    op.drop_table("model_verifications")
