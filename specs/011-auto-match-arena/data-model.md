# Data Model: Auto-Match Arena

## Changes to Existing Entities

### Match — new `match_kind` column

**Purpose**: Distinguishes admin-created matches from system-managed ones.

**Table**: `matches` (existing)

**New field**:
| Field | Type | Constraints | Description |
|---|---|---|---|
| `match_kind` | String(32) | NOT NULL, default `"manual"` | `manual` \| `practice_arena` \| `auto_scheduled` |

**Index**: `ix_matches_match_kind` on `match_kind` — allows `WHERE match_kind = 'practice_arena'` without a full scan.

**All existing rows** get `match_kind = "manual"` via migration default. No data rewrite needed.

---

## Python Model Addition

In `app/models/match.py`, add a `MatchKind` enum and the new column:

```python
class MatchKind(str, enum.Enum):
    MANUAL = "manual"
    PRACTICE_ARENA = "practice_arena"
    AUTO_SCHEDULED = "auto_scheduled"


class Match(Base):
    # ... existing fields ...
    match_kind: Mapped[MatchKind] = mapped_column(
        FlexibleEnumType(MatchKind, length=32),
        nullable=False,
        default=MatchKind.MANUAL,
        server_default="manual",
        index=True,
    )
```

---

## Migration

**File**: `migrations/versions/0019_add_match_kind.py`

```python
"""add match_kind to matches

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"

def upgrade() -> None:
    with op.batch_alter_table("matches") as b:
        b.add_column(
            sa.Column(
                "match_kind",
                sa.String(32),
                nullable=False,
                server_default="manual",
            )
        )
        b.create_index("ix_matches_match_kind", ["match_kind"])

def downgrade() -> None:
    with op.batch_alter_table("matches") as b:
        b.drop_index("ix_matches_match_kind")
        b.drop_column("match_kind")
```

---

## No New Tables

This feature requires no new tables. All state is carried on the existing `matches`, `players`, `bots`, and `strategy_prompts` tables.
