"""add user_milestones table, signup-source columns, and users.is_internal

Schema only. Both backfills (reconstructing historical milestones, and flagging
existing internal accounts) land in later migrations so this one can be reviewed
and reasoned about on its own — it is purely additive and every new column is
nullable or carries a server default, so the old code keeps serving while it runs.

That last point is load-bearing: railway.json runs `alembic upgrade head` as a
preDeployCommand, so this executes against production while the previous release
is still handling requests.

Revision ID: 0052
Revises: 0051
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0052"
down_revision: Union[str, None] = "0051"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_milestones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("milestone", sa.String(length=32), nullable=False),
        sa.Column("reached_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_match_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_milestones_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_milestones"),
        # The idempotency guarantee: recording a milestone twice is a no-op rather
        # than a duplicate row. The recorder relies on this raising so it can
        # swallow the collision and move on.
        sa.UniqueConstraint("user_id", "milestone", name="uq_user_milestones_user_id"),
    )
    op.create_index(
        "ix_user_milestones_user_id", "user_milestones", ["user_id"], unique=False
    )
    # Every page query filters by milestone and then by when it was reached.
    op.create_index(
        "ix_user_milestones_milestone_reached_at",
        "user_milestones",
        ["milestone", "reached_at"],
        unique=False,
    )

    # Plain ADD COLUMN works on SQLite, so no batch_alter_table is needed here;
    # batch mode is only required for the drops in downgrade().
    for column in (
        sa.Column("first_utm_source", sa.String(length=120), nullable=True),
        sa.Column("first_utm_medium", sa.String(length=120), nullable=True),
        sa.Column("first_utm_campaign", sa.String(length=120), nullable=True),
        sa.Column("first_referrer_host", sa.String(length=255), nullable=True),
        sa.Column("first_landing_path", sa.String(length=255), nullable=True),
        sa.Column("first_source_channel", sa.String(length=16), nullable=True),
    ):
        op.add_column("users", column)

    # Existing rows all become external. The separate backfill migration flags the
    # known internal accounts; until it runs, the dashboard over-counts rather than
    # under-counts, which is the safer direction to be wrong in.
    op.add_column(
        "users",
        sa.Column(
            "is_internal", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_internal")
        batch_op.drop_column("first_source_channel")
        batch_op.drop_column("first_landing_path")
        batch_op.drop_column("first_referrer_host")
        batch_op.drop_column("first_utm_campaign")
        batch_op.drop_column("first_utm_medium")
        batch_op.drop_column("first_utm_source")

    op.drop_index("ix_user_milestones_milestone_reached_at", table_name="user_milestones")
    op.drop_index("ix_user_milestones_user_id", table_name="user_milestones")
    op.drop_table("user_milestones")
