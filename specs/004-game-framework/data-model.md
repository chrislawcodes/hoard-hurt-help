# Data Model: Turn-Based Game Framework

## Entities

### Game (MODIFIED)

Add one column. Everything else unchanged.

| Field | Change | Notes |
|-------|--------|-------|
| game_type | ADD `String(64)`, NOT NULL, indexed | Which game module runs this game; backfilled to `"hoard-hurt-help"` |

### TurnSubmission (UNCHANGED — deferred)

Per plan Decision 3, the PD-typed columns (`action`, `target_player_id`, `points_delta`, `round_score_after`, `was_defaulted`, `message`) **stay as-is** and become *PD's* storage, written/read only by the PD module. A generic `move`/`score` JSON shape is **deferred** to when game #2 is built (keeps the engine regression tests unmodified; avoids generalizing storage with no second consumer).

### Player (UNCHANGED — deferred)

`current_round_score`, `total_round_score`, `total_round_wins` stay as PD's per-player state. A generic score + game-specific `state` JSON is deferred alongside game #2.

### GameModule (code, not a table)

The plugin implementing the contract for one `game_type`, held in a process-level registry. PD's module is an adapter over `app/engine/*`.

---

## Migrations

### `migrations/versions/0004_game_type.py` — data-affecting (benign)

> ⚠️ Data-affecting per the data-critical-waves rule: adds a NOT NULL column and backfills existing rows. No row deletion, no move-data rewrite (unlike `0003`). Review before prod apply; test DB builds from model metadata so it's unaffected. down_revision = `0003`.

Upgrade:
1. `op.add_column("games", sa.Column("game_type", sa.String(64), nullable=True))`
2. `op.execute("UPDATE games SET game_type = 'hoard-hurt-help' WHERE game_type IS NULL")`
3. `batch_alter_table("games")`: alter `game_type` to NOT NULL; add `ix_games_game_type`.

Downgrade: drop the index + column.

---

## Type sketch

```python
# app/games/base.py
from dataclasses import dataclass
from typing import Any, Protocol

class GameError(Exception):
    """Raised by a module on an illegal move; surfaced as a generic 400."""


@dataclass(frozen=True)
class GameConfig:
    total_rounds: int
    turns_per_round: int
    per_turn_deadline_seconds: int
    min_players: int
    max_players: int
    simultaneous: bool = True


class Game(Protocol):
    game_type: str
    def config_defaults(self) -> GameConfig: ...
    def rules_text(self) -> str: ...
    def validate_move(self, move: dict[str, Any], *, player, players) -> None: ...
    async def record_submission(self, db, turn, player, move: dict[str, Any]): ...
    async def resolve_turn(self, db, turn) -> None: ...
    async def award_round(self, db, game, round_num: int) -> None: ...
    async def finalize(self, db, game) -> None: ...
    def move_effect(self, submission) -> tuple[int, int | None]: ...
```
