# Plan Summary: Turn-Based Game Framework

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/games/base.py` | create | `Game` Protocol, `GameConfig`, `GameError` |
| `app/games/__init__.py` | create | registry: `register` / `get(game_type)` / `known_types`; registers PD on import |
| `app/games/hoard_hurt_help/__init__.py` | create | |
| `app/games/hoard_hurt_help/game.py` | create | PD adapter — delegates to `app/engine/*` (unchanged) |
| `app/engine/*` (resolver, rules, game_records, opponent_stats, board_signals, turn_summary, game_insights) | **unchanged** | PD's logic; tests import these directly — must stay put |
| `app/models/game.py` | modify | add `game_type` column |
| `app/engine/scheduler.py` | modify | turn loop calls `registry.get(game.game_type).{resolve_turn,award_round,finalize}` |
| `app/routes/agent_api.py` | modify | `/submit` uses `module.validate_move` + `record_submission`; payload uses `module.rules_text()` |
| `app/routes/agent_next_turn.py` | modify | payload via module |
| `app/routes/web.py` + viewer templates | modify | move-effect via `module.move_effect` (generic fallback) |
| `app/routes/admin_api.py` / `admin_web.py` | modify | game creation sets `game_type` (default `hoard-hurt-help`) |
| `app/schemas/agent.py` | modify | HOARD/HELP/HURT Literal becomes PD-owned; submit takes a generic module-validated move |
| `migrations/versions/0004_game_type.py` | create | add `games.game_type`, backfill `hoard-hurt-help` |
| `tests/test_game_registry.py`, `tests/test_stub_game.py` | create | registry + conformance stub (SC-002) |
| existing engine tests | **unchanged** | regression gate |

## Migration Steps
1. `add_column games.game_type (String(64), nullable=True)`.
2. `UPDATE games SET game_type='hoard-hurt-help' WHERE game_type IS NULL`.
3. `batch_alter_table('games')`: set `game_type` NOT NULL; add `ix_games_game_type`.

> ⚠️ Data-affecting (benign: column add + backfill, no deletion/rewrite). Review before prod. Tests build from metadata → unaffected. down_revision `0003`.

## Data Model
- **Game**: `games` — gains `game_type` (NOT NULL, default `hoard-hurt-help`).
- **TurnSubmission / Player**: UNCHANGED — PD's typed columns stay as PD's storage; generic move/state JSON deferred to game #2.

## Key Constraints
- **PD adapter over unchanged engine; engine tests pass unmodified** — *Why: regression gate; preserves PD math + test imports.*
- **Platform calls only the contract via the registry** — *Why: a new game plugs in with zero platform edits (SC-002).*
- **`get(game_type)` raises on unknown; poller skips** — *Why: one bad/unregistered game must not crash others (SC-004).*
- **`validate_move` failure → generic `400 INVALID_MOVE`** — *Why: platform must not assume a game's move shape.*
- **Storage generalization deferred; only add `game_type`** — *Why: Option B, no game #2 yet; avoids breaking the tests-unmodified gate.*
- **Conformance stub proves SC-002** — *Why: executable check that the contract is real + partial n=1 insurance.*
