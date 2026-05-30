"""drop the reusable strategy_profiles table

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-30

DATA-AFFECTING (data-critical-waves): drops the `strategy_profiles` table and
all rows in it. This is INTENTIONAL — the reusable per-user strategy library is
removed in favor of per-game strategy chosen at entry (a preset or free text,
served by the game module). Saved profiles are not migrated anywhere; per-game
strategies live in `strategy_prompts` and are unaffected. Review before prod
apply. The test DB builds from model metadata, not this migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_strategy_profiles_user_id", table_name="strategy_profiles")
    op.drop_table("strategy_profiles")


def downgrade() -> None:
    op.create_table(
        "strategy_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_strategy_profiles_user_id_name"),
    )
    op.create_index("ix_strategy_profiles_user_id", "strategy_profiles", ["user_id"])
