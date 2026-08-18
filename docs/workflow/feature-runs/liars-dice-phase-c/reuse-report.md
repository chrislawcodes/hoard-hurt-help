# Reuse Report — Liar's Dice (Phase C)

Adversarial reuse audit. Verdicts: **reuse** (use as-is), **extend** (small backward-compatible
seam), **justified-new** (genuinely LD-specific). 14/26 reuse, 7 extend, 4 justified-new,
**zero PD breakage** (every new seam has a PD-reproducing default).

| Capability | Existing module (path:symbol) | Verdict | Note |
|---|---|---|---|
| Module base + protocol | `app/games/base.py:BaseGameModule` | reuse | LD subclasses it; overrides hooks. |
| Registration | `app/games/__init__.py:register/get` | reuse | `register(LiarsDice())` on import. |
| Per-match state | `app/models/game_state.py:MatchState` | reuse | hand/standing bid/active actor/wild/config in `state_json`. |
| Per-player state | `app/models/game_state.py:PlayerState` | reuse | dice + count in `state_json`. No migration. |
| Sequential driver | `app/engine/turn_drivers.py:SequentialDriver` | reuse | selected by `simultaneous=False`. |
| Move submit plumbing | `app/engine/agent_play.py:submit_action/_pack_move` | reuse | free-form `move` already passes through. |
| TurnSubmission quantity/face | `app/models/turn.py:TurnSubmission.quantity/.face` | reuse | already nullable (migration 0033); PD leaves NULL. |
| `_drop_empty_game_state` guard | `app/schemas/agent.py` | reuse | keeps PD payload byte-identical when state empty. |
| final_placement | `app/games/base.py:BaseGameModule.final_placement` | reuse(override) | LD returns elimination order. |
| match_placement_key | `app/read_models/leaderboard.py` calls it | reuse(override) | **must override** or LD ranks by PD proxy. |
| move_effect / theme | `app/games/base.py` | reuse(override) | LD: `(0,None)` + LD palette. |
| rules_text / agent_base_prompt | `app/games/hoard_hurt_help/game.py` | reuse pattern | new LD content, same hook shape. |
| strategy presets | `app/games/hoard_hurt_help/strategy.py` | reuse pattern | new LD presets file. |
| Bot infra (seating/roster/presets) | `app/engine/sims/{seating,roster,presets}.py` | reuse | LD reuses seating/profiles/Practice-Arena wiring. |
| Test harness | `tests/test_sequential_driver.py`, `tests/test_stub_game.py`, `tests/test_migrations.py` | reuse + extend | mirror for LD module/driver/engine. |
| **Bot decision seam** | `app/engine/turn_drivers.py:_drive_actor_turn` (calls `default_move`) | **extend** | add `bot_move(db,match,player)` hook; `BaseGameModule` default = `default_move`. PD unaffected. |
| **Validation snapshot** | `app/engine/agent_play.py:submit_action` before `validate_move` | **extend** | inject read-only `{standing_bid,dice_counts,active_actor,total_dice,wild}` into move dict via a `GameModule` hook (default no-op). |
| **Game-aware player bounds** | `app/schemas/admin.py:CreateGameRequest`, route validators | **extend** | validate against `module.config_defaults().min_players..max_players` **in route validators only**. |
| **Per-match config persist** | `app/engine/match_creation.py:create_match` (no MatchState init) | **extend** | route seeds `MatchState.state_json["config"]` at create; do NOT add params to `create_match`. |
| **Public action schema** | `HistoryAction` (`app/schemas/agent.py`), `TimelineAction` (`app/read_models/matches.py`), `SpectatorAction` (`app/schemas/spectator.py`) | **extend** | add optional `quantity`/`face` (nullable); PD null. |
| **Public-state plumbing** | `app/routes/web_viewer.py:_game_view_context`, `app/routes/spectator_api.py` | **extend** | call `module.public_state_for()`; PD returns empty → omitted. |
| **Bid/raise/ace rules** | — | justified-new | pure `engine.py` LD math. |
| **Dice roll (seeded)** | — | justified-new | `roll(n, rng)` in `engine.py`; Sims reuse it. |
| **Showdown count (face+wilds)** | — | justified-new | `count_for`/`resolve_showdown`. |
| **LD bot strategy** | `app/engine/sims/strategies.py` (PD vocab) | extend/justified-new | new LD decision logic; reuse profile/seed structure. |

## Non-negotiables (shared code that must NOT be tightened/broken)

1. **Player-range validation stays in route validators, not `create_match()`** —
   `app/engine/arena.py` creates HHH with `min_players=1`; tightening the shared core breaks
   arena/Practice-Arena. Keep `create_match` bounds loose (1..20); enforce game-aware bounds
   at the request layer only.
2. **Do not add LD params to `create_match()`** — arena/other callers don't pass them. The
   admin route seeds `MatchState` config instead.
3. **Bot seam must be game-agnostic** — `bot_move` returns a generic move dict; default falls
   back to `default_move`; PD never overrides.
4. **Leaderboard uses the `match_placement_key` override, no `game_type` branching** in
   `leaderboard.py`.
5. **Public schema widening is additive + nullable** — PD payloads stay byte-identical
   (guarded by `_drop_empty_game_state` and Pydantic exclude-none).

## Duplication risks
- None building a parallel engine: confirmed `app/games/liars_dice/` is **absent** on this
  branch (the Direct-Path PR 371 is not merged here), so FF builds the engine fresh.
- Avoid a `game_type` switch growing inside `web_viewer._game_view_context`; route LD view
  data through `public_state_for()` + a per-game template fragment instead.
