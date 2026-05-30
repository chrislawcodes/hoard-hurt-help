# Data Model: Live Connection Handshake for Bot Onboarding

## Entities

### Entity 1: Bot (modified)

**Purpose**: A persistent agent owned by a user. This feature adds one fact: whether the bot has ever successfully connected.

**Storage**: existing `bots` table.

**New field**:
| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| first_connected_at | DateTime(timezone=True) | NULL allowed | UTC time of the bot's first successful authenticated agent call. `NULL` = never connected. Set once, never updated. |

**Indexes**: none added (looked up only by the owner on the bot detail page, already keyed by `id`).

**Validation Rules**: write-once — set only on the `NULL → now` transition inside `require_bot`; never reset (a deliberate reissue does not clear it).

### Entity 2: Derived onboarding state (not persisted)

**Purpose**: The panel's state, computed at render time. No table.

**Derived from**:
- `Bot.first_connected_at` → connected?
- `Player` rows for the bot (`left_at IS NULL`) joined to `Game.state` → in a game? pre-game vs active?
- existence of a non-defaulted `TurnSubmission` for any of the bot's players → has moved?

**Resolution precedence**: has-moved → in-active-game → connected-pregame → connected-no-game → waiting-in-game → waiting. (See plan.md state table.)

### Entity 3: Per-bot live event (not persisted)

**Purpose**: Real-time nudge to an open detail page.

**Channel**: `bot:{bot_id}` (in-process pub/sub, `app/broadcast.py`).

**Events**:
| Event | Emitted when | Payload | Carries secret? |
|-------|--------------|---------|-----------------|
| `connected` | first successful auth for the bot (`first_connected_at` set) | `{}` (re-fetch trigger) | No |
| `moved` | bot's first non-defaulted submission | `{}` (re-fetch trigger) | No |

## Type Definitions

```python
# app/models/bot.py  (added field)
first_connected_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True), nullable=True
)
```

```python
# app/engine/bot_activity.py  (state enum + resolver, illustrative)
from enum import Enum

class OnboardingState(str, Enum):
    WAITING = "waiting"
    WAITING_IN_GAME = "waiting_in_game"
    CONNECTED_NO_GAME = "connected_no_game"
    CONNECTED_PREGAME = "connected_pregame"
    IN_GAME_NO_MOVE = "in_game_no_move"
    PLAYING = "playing"
```

## Migrations

```python
# migrations/versions/0005_add_bot_first_connected_at.py  (shape)
def upgrade() -> None:
    op.add_column(
        "bots",
        sa.Column("first_connected_at", sa.DateTime(timezone=True), nullable=True),
    )

def downgrade() -> None:
    op.drop_column("bots", "first_connected_at")
```

**Notes**:
- Additive, nullable, **no backfill** — existing bots become `NULL` ("never connected"), handled by play-history precedence in the resolver.
- `add_column` is SQLite-safe (no batch mode, no `drop_constraint`); local dev/tests get the column via `Base.metadata.create_all`. The migration targets Postgres prod.
- Verify post-apply: `bots` row count unchanged; column present.
