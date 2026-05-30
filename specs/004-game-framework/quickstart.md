# Quickstart: Turn-Based Game Framework

## Prerequisites
- [ ] App running; migration `0004` applied (adds `games.game_type`).
- [ ] PD module registered as `hoard-hurt-help`.

## US1 — PD plays unchanged
**Goal**: no regression.
- Run the existing engine suite: `pytest tests/test_resolver.py tests/test_end_to_end.py tests/test_board_signals.py tests/test_opponent_stats.py tests/test_turn_summary.py -q`.
- **Expected**: all pass with **no edits** to those files; a full PD game scores identically (HOARD +2, HELP +4, HURT −4, mutual-help +4, floor at 0, round wins, finalize winner).

## US2 — Add a game by writing only a module
**Goal**: the framework capability.
- Run `pytest tests/test_stub_game.py -q`.
- **Expected**: a game of `game_type="stub"` can be created, a bot can play it via `get_next_turn`/`submit`, and it resolves/scores — and the stub's diff touches only the module + its registration (no platform files).

## US3 — Mixed types coexist
- Create one PD game and one stub game; both appear in the lobby with their type, both schedule/start/run/finalize through the same platform machinery.
- A bot entered in both is handed each game's turn via `get_next_turn`.

## US4 — Module-driven validation
- Submit an illegal move for a type → generic `400 INVALID_MOVE`.
- Submit valid moves → the module resolves and the platform persists results.

## Troubleshooting
- **Unknown game_type** → `get()` raises; the poller skips that game (others unaffected).
- **Existing PD games after migration** → all have `game_type="hoard-hurt-help"` and play normally.
