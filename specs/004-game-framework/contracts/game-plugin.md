# Contract: the Game plugin interface

A game module implements the `Game` protocol and registers itself by `game_type`.
The platform never imports a specific game — it resolves the module from the
registry and calls only these methods.

## Registry (`app/games/__init__.py`)

```
register(module)        # add a module to the registry (keyed by module.game_type)
get(game_type) -> Game  # resolve a module; raises GameError("unknown game_type") if absent
known_types() -> list[str]
```
PD registers itself on import: `register(HoardHurtHelp())`.

## The `Game` protocol (`app/games/base.py`)

| Member | Purpose | Platform caller |
|--------|---------|-----------------|
| `game_type: str` | Identity / registry key | registry, Game.game_type |
| `config_defaults() -> GameConfig` | rounds, turns_per_round, deadline, min/max players, simultaneous | game creation, scheduler |
| `rules_text() -> str` | Rules shown to the agent | agent payload (turn / next-turn) |
| `validate_move(move, *, player, players)` | Validate a submitted move; raise `GameError` if illegal | `POST /submit` |
| `record_submission(db, turn, player, move)` | Persist the move into the module's storage | `POST /submit` |
| `resolve_turn(db, turn)` | Apply payoffs, persist deltas, mark resolved | scheduler turn loop |
| `award_round(db, game, round_num)` | End-of-round scoring | scheduler turn loop |
| `finalize(db, game)` | Pick winner, complete the game | scheduler turn loop |
| `move_effect(submission) -> (actor_delta, target_delta\|None)` | Per-move display for the spectator viewer (generic fallback if not provided) | web viewer |

## Rules the platform enforces (so a game can't break the platform)

- `get(game_type)` MUST raise a generic error for an unregistered type; the
  scheduler poller MUST skip such a game rather than crash others.
- `validate_move` rejection MUST surface as the standard `400` error envelope
  (generic `INVALID_MOVE`), not a game-specific code.
- Submitted move output is constrained by `validate_move`; the platform persists
  only what the module records.

## PD module mapping (`hoard-hurt-help`)

The PD adapter implements the protocol by delegating to the **unchanged** engine:
- `config_defaults()` → current PD defaults (10 rounds × 10 turns, 60s, 3–100 players, simultaneous)
- `rules_text()` → `app.engine.rules.RULES_TEXT_V1`
- `validate_move()` → the HOARD/HELP/HURT + target checks currently inline in `agent_api`
- `record_submission()` → write `TurnSubmission(action, target_player_id, message)`
- `resolve_turn` / `award_round` / `finalize` → `app.engine.resolver.{resolve_turn, award_round_winners, finalize_game}`
- `move_effect()` → the current `_move_effect` in `web.py`

## Conformance stub (test double)

A trivial module (`tests/`) implementing the protocol (e.g. moves = `{"action": " poke"}`, +1 each) registered under `game_type="stub"`, used to assert a game can be created/played/resolved touching only the module (SC-002).
