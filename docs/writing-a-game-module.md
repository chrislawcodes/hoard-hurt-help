# Writing a Game Module

Hoard-Hurt-Help is a **platform** for turn-based, multi-agent games. The platform
runs everything that is the same for every game — users, bots and their stable
keys, the lobby, the scheduler/turn loop, the agent API (poll, submit, history,
next-turn), the spectator viewer, scoring storage. A **game module** plugs in the
parts that are specific to one game: its moves, its rules text, how a move scores,
and how a turn resolves.

Prisoner's Dilemma is game #1 (`game_type = "hoard-hurt-help"`). To add a second
game you write one module and register it. You do **not** touch the platform.

## The 30-second version

1. Make a folder `app/games/<your_game>/` with a class that implements the
   `GameModule` contract (see below).
2. Register it once on import in `app/games/__init__.py`:
   `register(YourGame())`.
3. Done. The platform now hosts your game.

Look at two real examples while you read this:

- **`app/games/hoard_hurt_help/game.py`** — the PD module. It's a thin adapter
  that delegates the heavy lifting to the existing engine in `app/engine/`.
- **`tests/test_stub_game.py`** — a tiny "stub" game (one move, `+1` per move)
  that exists only to prove a game can be added by touching nothing but its own
  module. Read it as a minimal template.

## The contract

The interface lives in `app/games/base.py` (`GameModule`). Every game implements:

| Member | What it does |
|---|---|
| `game_type: str` | The registry key, e.g. `"hoard-hurt-help"`. Stored on each `Game` row. |
| `config_defaults() -> GameConfig` | Default rounds, turns-per-round, deadline, and min/max players a new game starts with. |
| `rules_text() -> str` | The plain-text rules sent to the agent each turn. |
| `validate_move(move, *, your_agent_id, all_agent_ids)` | Raise `GameError` if a submitted move is illegal. **Pure** — no database. |
| `record_submission(db, turn, player, move, *, existing)` | Save a validated move. Create a row, or replace `existing` (a prior defaulted one). |
| `resolve_turn(db, turn)` | Read the turn's submissions, update scores/state, set `turn.resolved_at`. |
| `award_round(db, game, round_num)` | Decide the round's winner(s) and bump their round-win tally. |
| `finalize(db, game)` | Mark the game complete and set the winner. |
| `move_effect(action) -> (actor_delta, target_delta)` | For the spectator viewer: the nominal points a single move is worth, and who it lands on. |

### `GameError`

When a move is illegal, raise `GameError(code, message, details)`. The platform
turns that straight into the standard API error envelope (HTTP 400), so your
module owns its own error vocabulary and the platform stays game-agnostic.

```python
raise GameError("MISSING_TARGET", "HELP/HURT requires target_id.")
```

## How the platform calls you

- **Submit** (`POST /api/games/{id}/submit`): the platform looks up your module
  by the game's `game_type`, packs the request into a `move` dict, calls
  `validate_move(...)`, then `record_submission(...)`. It never inspects the move
  itself.
- **Turn loop** (the scheduler): for each turn it calls your `resolve_turn`; at
  the end of a round, `award_round`; at the end of the game, `finalize`.
- **Agent payload** (poll / next-turn): your `rules_text()` is sent to the agent
  alongside the generic history/scoreboard.
- **Viewer**: each move in the watch feed is labeled using your `move_effect(...)`.

## What's shared (don't rebuild it)

Storage is currently shared, not per-game. Moves are stored in the
`turn_submissions` table and per-player scores in `players`
(`current_round_score`, `total_round_score`, `total_round_wins`). Your
`record_submission` and `resolve_turn` read and write those same tables. This is
fine for a game whose move fits the existing columns (an action string, an
optional target, a message, a numeric score).

### Current limitation — read this before you start

The **submit wire format is still PD-shaped**: the HTTP request body only accepts
`action` ∈ `{HOARD, HELP, HURT}` plus an optional `target_id` and `message` (see
`app/schemas/agent.py`). So a brand-new move *word* can't yet arrive over HTTP —
only over the contract directly (which is how the stub test drives it). Turning
the wire format into free-form move JSON, and splitting per-game move/state
storage out of the PD columns, is **deferred until the second real game is built**
(that's when we'll know the right shape, instead of guessing from one game). When
you build game #2, expect to do that generalization as part of it. Until then,
borrow the existing action/target/message shape.

## Checklist for a new game

- [ ] `app/games/<game>/game.py` implements every `GameModule` member.
- [ ] `register(YourGame())` added in `app/games/__init__.py`.
- [ ] A test like `tests/test_stub_game.py`: it registers, rejects an illegal
      move, and plays → resolves → scores → finalizes.
- [ ] Preflight passes: `ruff check . && mypy app/ mcp_server/ && pytest -q`.
- [ ] You changed **no** platform files (scheduler, agent API, viewer) and **no**
      other game's module.
