"""Add preset Sim metadata to bots.

Revision ID: 0017
Revises: 0016
Create Date: 2026-06-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("sim_profile_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bots",
        sa.Column("sim_profile_name", sa.String(length=120), nullable=True),
    )
    with op.batch_alter_table("bots") as batch_op:
        batch_op.create_unique_constraint(
            op.f("uq_bots_user_id_sim_profile_id"),
            ["user_id", "sim_profile_id"],
        )
    op.create_index(
        op.f("ix_bots_sim_profile_id"),
        "bots",
        ["sim_profile_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_bots_sim_profile_id"), table_name="bots")
    with op.batch_alter_table("bots") as batch_op:
        batch_op.drop_constraint(
            op.f("uq_bots_user_id_sim_profile_id"), type_="unique"
        )
    op.drop_column("bots", "sim_profile_name")
    op.drop_column("bots", "sim_profile_id")
