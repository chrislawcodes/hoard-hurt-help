# Tasks — Liar's Dice (Phase C)

Executable slices from `plan.md`. Each `[CHECKPOINT]` is a diff-review boundary (≤ ~300 code
lines; tests may push a slice higher but carry less regression risk). Preflight
(`ruff`, `mypy app/ mcp_server/`, `pytest -q`) must be green at every checkpoint. PD parity is
the merge gate. `[P]` = safe to parallelize (disjoint files, depends only on the engine interface).

## Slice 1 — Pure rules engine `[CHECKPOINT]`
- [ ] `app/games/liars_dice/__init__.py` (package).
- [ ] `app/games/liars_dice/engine.py` (pure; imports only `GameError`): `Bid`, `BidMove`,
      `ChallengeMove`, `Move`, `parse_move`, `count_for` (wild aces; no double-count of 1s),
      `resolve_showdown` (count==quantity holds), `is_legal_raise` (strictly-higher + Dudo ace
      rules), `min_legal_raise` (None at ceiling; gotcha (a): wild=False after (q,6) → (q+1,1);
      gotcha (b): aces at qty k sort between (k-1,6) and (k,2)), `roll(n, rng)`.
- [ ] verify: `python3 -c "import app.games.liars_dice.engine"`; ruff + mypy clean on the file.

## Slice 2 — Engine unit tests `[CHECKPOINT]` `[P after 1]`
- [ ] `tests/test_liars_dice_engine.py`: exhaustive ace table for `is_legal_raise`; both gotchas
      explicitly; `count_for`/`resolve_showdown` boundaries; `roll` determinism by seed;
      **property test** — for every `(prev,total_dice,wild)`, if `min_legal_raise` is not None then
      `is_legal_raise(prev,result,wild=wild)` is True.
- [ ] verify: `pytest -q tests/test_liars_dice_engine.py`.

## Slice 3 — Platform seams + PD parity `[CHECKPOINT]` `[P after 1]`
- [ ] `app/games/base.py`: add `async def bot_move(self, db, match, player) -> dict` (default
      `return await self.default_move(...)`) and a validation-snapshot hook (default `{}`) to
      `GameModule`/`BaseGameModule`.
- [ ] `app/engine/turn_drivers.py`: `_drive_actor_turn` calls `module.bot_move(...)` for bot actors.
- [ ] `app/engine/agent_play.py`: `submit_action` merges the module's validation snapshot into the
      move dict before `validate_move`.
- [ ] verify: PD suite + `tests/test_stub_game.py` + `tests/test_sequential_driver.py` pass
      UNMODIFIED; full preflight green (R2).

## Slice 4 — Public action schema widening `[CHECKPOINT]` `[P after 1]`
- [ ] Add nullable `quantity`/`face` to `HistoryAction` (`app/schemas/agent.py`), `TimelineAction`
      (`app/read_models/matches.py`), `SpectatorAction` (`app/schemas/spectator.py`); add a
      `public_state` slot to the spectator/viewer payloads.
- [ ] verify: a test asserts PD's agent-poll + spectator JSON are byte-identical with the new
      keys absent/null (R2).

## Slice 5 — LD module: play `[CHECKPOINT]`
- [ ] `app/games/liars_dice/game.py` `class LiarsDice(BaseGameModule)`: `config_defaults`
      (simultaneous=False, admin_only=True, 3–6, 30s), `on_round_start` (roll dice → PlayerState,
      clear bid, set leader; guarded config read w/ fallback — R8), `next_actor`, `validate_move`
      (+ reads the injected snapshot; strips snapshot keys in record_submission), `record_submission`
      (BID/CHALLENGE → quantity/face; advance MatchState), `resolve_turn`, `private_state_for`,
      `public_state_for` (incl. `wild_ones`), `default_move`, `move_effect`→(0,None), `theme`.
- [ ] `app/games/liars_dice/rules_text.py` (per-match wild mode + submit JSON), `strategy.py`.
- [ ] `agent_base_prompt`; register `LiarsDice()` in `app/games/__init__.py`.
- [ ] verify: ruff + mypy + `python3 -c "import app.games"`; PD parity still green.

## Slice 6 — LD module: endgame `[CHECKPOINT]`
- [ ] `award_round` (showdown: read PlayerState dice, `resolve_showdown`, dock loser's die, write
      `last_showdown.revealed`), `is_match_over` (one player with dice), `finalize`,
      `final_placement` (elimination order), `match_placement_key` (elimination order, hands-won
      tiebreak — R7).
- [ ] verify: ruff + mypy; PD parity green.

## Slice 7 — Module / driver / security tests `[CHECKPOINT]`
- [ ] `tests/test_liars_dice_module.py`: validate_move rejects each illegal case; record_submission
      advances MatchState; award_round docks the right player; `match_placement_key` ranks by
      elimination (R7); MatchState/PlayerState in-place JSON round-trip (R3); resolve_turn resume
      idempotency — re-enter loop after unresolved CHALLENGE, exactly one die docked (R5).
- [ ] `tests/test_liars_dice_driver.py`: seeded 3-player match to completion (variable hands;
      winner = last standing).
- [ ] SC-HD: no other player's dice faces in agent-poll, MCP `get_game_state`, or spectator JSON
      pre-showdown; revealed dice appear post-showdown (R1).
- [ ] verify: `pytest -q tests/test_liars_dice_*`; full preflight.

## Slice 8 — LD bots `[CHECKPOINT]`
- [ ] `app/games/liars_dice/sims.py`: `decide(public_state, my_dice, *, seed) -> move`; P(bid holds)
      estimate from own dice + unknown count; raise when confident, challenge when improbable, bluff
      per personality; reads `wild_ones`. Stable seed (hashlib, decision 7). Wire into `bot_move`.
- [ ] verify: bots-only seeded match runs to completion in BOTH wild modes; SAME winner for SAME
      seed across two runs and under a different `PYTHONHASHSEED` (R4 + decision 7).

## Slice 9 — Admin create-match `[CHECKPOINT]`
- [ ] Game-aware player bounds in route validators (`admin_api.py`, `game_admin_api.py`,
      `game_admin_web.py`) reading `config_defaults().min_players/max_players`; leave
      `create_match`/`arena` bounds untouched.
- [ ] `game_type` selector + LD-only wild on/off (default on) + dice-per-player (default 5) fields;
      seed `MatchState.config` atomically in the create transaction (decision 3).
- [ ] `matches_user.py` `/games/{game}/matches/new` rejects `admin_only` games.
- [ ] verify: PD create + arena create still work; user route rejects LD create; LD match starts.

## Slice 10 — Minimal viewer `[CHECKPOINT]`
- [ ] `templates/fragments/liars_dice_*.html`: bid feed + showdown reveal + per-player dice-count
      bars. `app/routes/web_viewer.py` + `app/routes/spectator_api.py` source LD public state from
      `public_state_for()` (no `game_type` switch in `_game_view_context`). `app/static/style.css`
      LD theme tokens.
- [ ] verify: render an LD match in the viewer; SC-HD spectator sweep still green; full preflight.

## Final
- [ ] All slices green; PD suite + stub unmodified; full preflight green; LD registered
      `admin_only=True` (invisible to non-admins).
