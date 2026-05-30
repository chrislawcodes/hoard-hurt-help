# Data Model: Persistent Bots

## Entities

### Bot (NEW)

**Purpose**: A persistent agent owned by a user. Holds the one stable credential pasted into the MCP client. The unit that enters games.

**Storage**: table `bots`

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | int | PRIMARY KEY | Bot id |
| user_id | int | FK→users.id, NOT NULL, INDEX | Owner |
| name | str(64) | NOT NULL | Display name; unique per user (see Indexes) |
| key_lookup | str(64) | NOT NULL, UNIQUE, INDEX | `sha256(full_key)` hex — O(1) auth lookup |
| key_hint | str(8) | NOT NULL | Last 4 chars of the key, for display (non-secret) |
| status | enum(active,paused) | NOT NULL, default `active` | Kill switch |
| paused_at | datetime(tz) | NULL | When paused |
| paused_reason | str(120) | NULL | e.g. "owner", "auto: stalled" |
| max_concurrent_games | int | NOT NULL, default 3 | Owner cap (token-budget guardrail) |
| stall_threshold | int | NOT NULL, default 3 | Consecutive missed turns before flag/auto-pause |
| created_at | datetime(tz) | NOT NULL, server default now | |

**Indexes**: `UNIQUE(key_lookup)`; `UNIQUE(user_id, name)` (a user's bot names are distinct); `INDEX(user_id)`.

**Relationships**: `Bot 1—* Player`; `Bot *—1 User`.

**Validation**: name 1–64 chars; `max_concurrent_games >= 1`; key never stored or logged in plaintext — only `key_lookup` (hash) + `key_hint`.

---

### StrategyProfile (NEW)

**Purpose**: A named, reusable strategy owned by a user. Seeds a player's strategy at entry.

**Storage**: table `strategy_profiles`

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | int | PRIMARY KEY | |
| user_id | int | FK→users.id, NOT NULL, INDEX | Owner |
| name | str(64) | NOT NULL | Unique per user |
| prompt_text | Text | NOT NULL | Strategy text |
| is_default | bool | NOT NULL, default false | At most one default per user (enforced in app logic) |
| created_at | datetime(tz) | NOT NULL, server default now | |
| updated_at | datetime(tz) | NOT NULL, server default now, onupdate now | |

**Indexes**: `UNIQUE(user_id, name)`; `INDEX(user_id)`.

**Relationships**: `StrategyProfile *—1 User`. No live link to players — its text is **copied** into a `StrategyPrompt` at entry (FR-016).

---

### Player (MODIFIED)

**Purpose**: A bot's participation in one game (existing). Now owned by a Bot.

**Changes to `players`**:

| Field | Change | Notes |
|-------|--------|-------|
| bot_id | ADD int, FK→bots.id, NOT NULL, INDEX | The owning bot |
| agent_key_hash | DROP | Per-game credential removed; auth moves to Bot |
| (constraint) | ADD `UNIQUE(bot_id, game_id)` | One player per bot per game (FR-010) |
| user_id | KEEP | Still FK→users.id (derivable via bot, but kept for existing queries) |
| agent_id | KEEP | In-game name; `UNIQUE(game_id, agent_id)` unchanged |

All other fields (`game_id`, `model_self_report`, `joined_at`, `left_at`, scores) unchanged.

---

### StrategyPrompt (UNCHANGED)

Remains the per-player working copy ([app/models/strategy_prompt.py](../../app/models/strategy_prompt.py)). At entry, a new row is created with `prompt_text` copied from the chosen `StrategyProfile`. The turn payload keeps reading the latest prompt for the player.

---

### Platform caps (NEW — config, not a table)

Added to `app/config.py` `settings`:
- `max_concurrent_active_games: int` — platform cap; entry/start refused beyond it.
- (`Game.max_players` already exists — reused as the per-game cap.)

---

### Subscription (FUTURE — NOT built)

Documented seam only. A future `subscriptions` table would carry `bot_id` (FK→bots), match criteria, and be read by the scheduler poller to auto-enter bots into new games. **Not created in this feature.**

---

## Type Definitions (illustrative)

```python
# app/models/bot.py
from __future__ import annotations
import enum
from datetime import datetime
from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class BotStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"


class Bot(Base):
    __tablename__ = "bots"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_bots_user_id_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_lookup: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    key_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[BotStatus] = mapped_column(
        Enum(BotStatus, native_enum=False, length=16), nullable=False, default=BotStatus.ACTIVE
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    max_concurrent_games: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    stall_threshold: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
```

```python
# app/engine/tokens.py (additions)
import hashlib, hmac, secrets

def generate_bot_key() -> str:
    """Stable per-bot credential. Format: sk_bot_<48 hex>."""
    return "sk_bot_" + secrets.token_hex(24)

def bot_key_lookup(key: str) -> str:
    """Indexed lookup handle for a bot key. sha256 is correct for a 192-bit random token."""
    return hashlib.sha256(key.encode()).hexdigest()

def bot_key_matches(presented: str, stored_lookup: str) -> bool:
    return hmac.compare_digest(bot_key_lookup(presented), stored_lookup)
```

---

## Migrations

### `migrations/versions/0003_persistent_bots.py` — DATA-AFFECTING

> ⚠️ **Data-critical.** Adding NOT NULL `players.bot_id` under a fresh-start cutover has no valid backfill, so this migration **clears throwaway in-flight game data** (turn_submissions, turns, strategy_prompts, players) before the schema change. Confirmed acceptable because prod data is throwaway. Per the data-critical-waves rule: review before prod apply; the test DB is built from model metadata so it is unaffected. Down-revision: `0002`.

Steps (upgrade):
1. `create_table('bots', ...)` with `UNIQUE(key_lookup)`, `UNIQUE(user_id, name)`, `INDEX(user_id)`.
2. `create_table('strategy_profiles', ...)` with `UNIQUE(user_id, name)`, `INDEX(user_id)`.
3. Clear dependent rows in FK-safe order: `turn_submissions` → `turns` → `strategy_prompts` → `players`.
4. `batch_alter_table('players')`: add `bot_id` (NOT NULL, FK→bots), add `UNIQUE(bot_id, game_id)`, drop `agent_key_hash`.

```sql
-- conceptual; implemented via Alembic op + batch_alter_table for SQLite
CREATE TABLE bots (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name VARCHAR(64) NOT NULL,
  key_lookup VARCHAR(64) NOT NULL,
  key_hint VARCHAR(8) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active',
  paused_at TIMESTAMPTZ NULL,
  paused_reason VARCHAR(120) NULL,
  max_concurrent_games INTEGER NOT NULL DEFAULT 3,
  stall_threshold INTEGER NOT NULL DEFAULT 3,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_bots_key_lookup ON bots(key_lookup);
CREATE UNIQUE INDEX uq_bots_user_id_name ON bots(user_id, name);

CREATE TABLE strategy_profiles (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name VARCHAR(64) NOT NULL,
  prompt_text TEXT NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_strategy_profiles_user_id_name ON strategy_profiles(user_id, name);

-- players: add bot_id, drop agent_key_hash, add UNIQUE(bot_id, game_id)
```

Downgrade: drop `UNIQUE(bot_id, game_id)`, re-add `agent_key_hash` (NULL), drop `bot_id`, drop `strategy_profiles`, drop `bots`. (Data is not restored — destructive upgrade is one-way for game rows.)
