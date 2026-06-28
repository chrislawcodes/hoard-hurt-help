"""Clear legacy model values on agent versions (finish the decouple).

Revision ID: 0043
Revises: 0042
Create Date: 2026-06-28

Migration 0040 made ``agent_versions.model`` nullable and new versions write
NULL, but it deliberately left existing rows untouched. Those legacy values
(e.g. ``gpt-5.4-mini``) are still the agent's stored model, and before the
payload guard they could force a seat onto a model its chosen provider can't run
(a Claude seat carrying a ``gpt-*`` model 404s the claude CLI every turn). This
is the matching data cleanup: every legacy version becomes model-less like a
freshly created one, so the agent truly carries no model.

Human seats store the sentinel ``model = 'human'`` and are preserved. Scoped by
the model value (not the agent kind) on purpose: ``agents.kind`` is stored in
mixed legacy forms (value ``'ai'`` and legacy name ``'AI'``), so a kind filter
could miss rows. The only reader of ``agent_versions.model`` is the turn payload,
which now guards the value, so clearing it cannot break any code path.

The statement is idempotent — re-running it is a no-op once the legacy values are
cleared.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0043"
down_revision: Union[str, None] = "0042"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Null every non-human model. Idempotent: only matches rows that still carry
    # a legacy value.
    op.execute(
        "UPDATE agent_versions SET model = NULL "
        "WHERE model IS NOT NULL AND model <> 'human'"
    )


def downgrade() -> None:
    # The pre-decouple model values are not recoverable, so there is nothing to
    # restore. Migration 0040's downgrade still re-imposes NOT NULL via a
    # sentinel if the schema itself is rolled back.
    pass
