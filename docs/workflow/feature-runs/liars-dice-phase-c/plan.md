# Plan — Liar's Dice (Phase C)

Build plan for the spec. Drives the design settled in `spec.md` + `reuse-report.md`. Every
new platform seam ships a PD-reproducing default; PD parity is the merge gate.

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Round 5 accepted; addressed in plan.md (not spec, to keep spec checkpoint healthy). Plan decision 7: bot seed derived from persisted state (match_id,hand,seat,bid_index) so resume re-decides identically. Plan decision 8: public_state carries wild_ones + full §7.2 shape. Plan reconciles §2-vs-§9: game-aware bounds in route validators only; plan is authoritative for the implementer.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Round 5: no new material gaps; viewer/spectator hidden-info + MatchState/PlayerState key layout verified by tests once files exist (plan slices 7/10, R1/R3/R5).
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: Accepted all. Fixed: bot seed uses hashlib (stable), not Python hash() (decision 7 + restart verification); MatchState seeded atomically in the create transaction (decision 3); fourth create flow matches_user.py added to slice 9 and must reject admin_only LD; SC-HD covers the MCP path (R1). PD JSON byte-identical assertion added (R2/slice 4).
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: Accepted: stable hashlib seed (decision 7); atomic MatchState write (decision 3); SC-HD MCP-path coverage (R1); PD JSON byte-identical test for new nullable keys (R2/slice 4). GameConfig.simultaneous is static per module (not mutated mid-match), so the mid-match-switch concern is a non-issue.

## Architecture decisions

1. **Pure engine is standalone and DB-free.** `app/games/liars_dice/engine.py` imports only
   `GameError` from `app/games/base.py`. Shared by the module and the Sims. This is the
   testable core; every ambiguous rule is decided here and pinned by unit tests.
2. **Two new platform seams, both with PD defaults** (reuse-report extends):
   - `async def bot_move(self, db, match, player) -> dict` on `GameModule`/`BaseGameModule`;
     `BaseGameModule.bot_move` returns `await self.default_move(...)`. `SequentialDriver._drive_actor_turn`
     calls `module.bot_move(...)` for bot actors instead of `default_move`. PD never overrides
     (simultaneous driver never calls it).
   - `def validation_snapshot(self, ...) -> dict` (or an injection hook) so the submit path
     merges a read-only `{standing_bid, dice_counts, active_actor, total_dice, wild}` block into
     the move dict before `validate_move`. `BaseGameModule` default returns `{}` (PD no-op). LD's
     `record_submission` strips these keys before persisting.
3. **Per-match config lives in `MatchState.state_json["config"]`, seeded ATOMICALLY at create.**
   The create route adds the `MatchState` row (with `config`) in the **same DB transaction** as
   the `Match` insert — it calls `create_match()` then adds `MatchState` on the same session
   before commit, so there is no half-initialized match (NOT by adding params to `create_match`).
   `on_round_start(round=1)` reads config through a guarded accessor that falls back to
   `config_defaults` if the row/keys are missing (defensive — handles a legacy/missing config, R6/R8).
4. **Game-aware player bounds at the request layer only.** Route validators fetch
   `get(game_type).config_defaults().min_players/max_players`. `create_match()` and `arena.py`
   keep their loose 1..20 bounds (non-negotiable #1/#2).
5. **Public state flows through the module, not `game_type` branches.** `public_state_for()` feeds
   the viewer + spectator JSON; a per-game template fragment renders it. Public action schemas gain
   nullable `quantity`/`face`. PD returns empty/None → omitted (byte-identical).
6. **Placement is per-game.** LD overrides both `final_placement` (elimination order) and
   `match_placement_key` (so the leaderboard ranks by finish order, not PD's round-wins proxy).
7. **Bot determinism comes from persisted state via a STABLE hash.** A bot's move seed is
   derived from durable values with `hashlib` (NOT Python's `hash()`, which is salted by
   `PYTHONHASHSEED` and differs across restarts — both plan reviewers flagged this):
   `int.from_bytes(hashlib.sha256(f"{match_id}:{hand}:{seat_name}:{bid_index}".encode()).digest()[:8])`
   fed into `random.Random(seed)`. `match_id` is a stable string PK assigned before play, so it
   is available. A resumed bot turn re-decides identically. `roll(n, rng)` takes the seeded
   `Random` so the deal is reproducible. verification: a restart test re-runs a bot turn under a
   different `PYTHONHASHSEED` and asserts the same move.
8. **`public_state` carries `wild_ones`.** The §7.2 public-state block includes
   `wild_ones: bool` (plus `hand`, `standing_bid`, `active_actor`, `dice_counts`,
   `bid_history`, `showdowns`) so bots and the viewer can tell wild from no-wild from the same
   surface. This supersedes spec §6's shorter list.

**Spec §2 vs §9 reconciliation (round-5):** the spec's §2 phrasing ("allow 3..6") is
superseded by the unambiguous rule here and in spec §9 — player bounds are **game-aware,
enforced in route validators only**; shared `create_match`/`arena` bounds are left untouched.
This plan is authoritative for the implementer.

## Slice breakdown (each a `[CHECKPOINT]`, ≤ ~300 code lines)

| # | Slice | Files | Est. code | Notes |
|---|---|---|---|---|
| 1 | Pure engine | `app/games/liars_dice/engine.py`, `__init__.py` | ~330 | Bid/moves/parse/count/showdown/legal-raise/min-raise/roll. |
| 2 | Engine tests | `tests/test_liars_dice_engine.py` | ~600 (test-only) | Exhaustive ace table; both gotchas; property test (every `min_legal_raise` ⇒ `is_legal_raise`); `roll` determinism. |
| 3 | Platform seams + parity | `app/games/base.py`, `app/engine/turn_drivers.py`, `app/engine/agent_play.py` | ~150 | `bot_move` + validation-snapshot hooks w/ PD defaults; driver calls `bot_move`; submit injects snapshot. Parity test: PD + stub unchanged. `[P after 1]` |
| 4 | Public schema widening | `app/schemas/agent.py`, `app/read_models/matches.py`, `app/schemas/spectator.py` | ~90 | nullable `quantity`/`face`; `public_state` slot. PD null. `[P after 1]` |
| 5 | LD module — play | `app/games/liars_dice/game.py`, `rules_text.py`, `strategy.py` | ~300 | config_defaults, on_round_start, next_actor, validate_move(+snapshot), record_submission, resolve_turn, private/public_state_for, default_move, rules_text, agent_base_prompt, move_effect, theme; register. |
| 6 | LD module — endgame | `app/games/liars_dice/game.py` | ~120 | award_round (showdown), is_match_over, finalize, final_placement, match_placement_key. |
| 7 | Module/driver/security tests | `tests/test_liars_dice_module.py`, `tests/test_liars_dice_driver.py` | ~450 (test-only) | validate_move cases; record_submission advances; showdown docks loser; placement order; seeded 3-player match to completion; SC-HD multi-channel leak test; MatchState/PlayerState round-trip; resolve_turn resume idempotency. |
| 8 | LD bots | `app/games/liars_dice/sims.py` + bot_move wiring | ~250 | P(bid holds) estimate; raise/challenge/bluff; both wild modes; deterministic by seed. Test: bots-only seeded match → fixed winner. |
| 9 | Admin create | `app/schemas/admin.py`, `app/routes/admin_api.py`, `app/routes/game_admin_api.py`, `app/routes/game_admin_web.py`, `app/routes/matches_user.py`, templates | ~290 | game-aware bounds; `game_type` + wild/dice fields; seed `MatchState.config` atomically at create. The user create flow `/games/{game}/matches/new` (`matches_user.py`, admin-reachable, hardcodes `_CREATE_DEFAULTS`) must **reject `admin_only` games** so there is no untouched path that creates LD without config. Test: PD/arena create still works; user route rejects LD create. |
| 10 | Viewer | `templates/fragments/liars_dice_*.html`, `app/routes/web_viewer.py`, `app/routes/spectator_api.py`, `app/static/style.css` | ~250 | bid feed + showdown reveal + dice-count bars via `public_state_for`. |

Order: 1 → (2,3,4 parallelizable) → 5 → 6 → 7 → 8 → 9 → 10. Slices 2/3/4 only depend on the
engine interface (slice 1) and touch disjoint files, so they are `[P]` candidates; the runner
records the parallel analysis before the tasks checkpoint. Slices 5–10 are sequential (5/6 share
`game.py`; 7 needs 5/6; 8 needs the bot seam from 3 + module from 5/6; 9/10 need the schema/seams).

## Residual risks (each with a pre-merge verification)

- **R1 — hidden-dice leak via a forgotten channel.** verification: SC-HD test (slice 7 + slice 10)
  drives to a pre-showdown state and asserts no other player's dice faces appear in (a) the agent
  API poll payload, (b) the **MCP `get_game_state` tool path explicitly** (a different execution
  path than the HTTP API — `app/mcp_server/`), and (c) the spectator JSON; after a showdown asserts
  the revealed dice DO appear in all three. Must be green pre-merge.
- **R2 — PD regression from shared-path edits (seams, schema, submit path).** verification: the PD
  test suite and `tests/test_stub_game.py` pass UNMODIFIED; full preflight (`ruff`, `mypy app/
  mcp_server/`, `pytest -q`) green on every slice; slice 3 adds an explicit PD-parity assertion;
  slice 4 adds a test asserting PD's agent-poll + spectator JSON are byte-identical with the new
  nullable `quantity`/`face`/`public_state` keys present (absent/null for PD).
- **R3 — `MatchState`/`PlayerState` in-place JSON edits not persisting.** verification: a round-trip
  test (slice 7) mutates `state_json` in place, commits, reloads in a new session, asserts the
  change survived (the classic `MutableDict` bug).
- **R4 — bot logic wedging the loop or being non-deterministic.** verification: a seeded bots-only
  3–6 player match (slice 8) runs to completion in both wild and no-wild modes and produces the
  SAME winner for the SAME seed across two runs.
- **R5 — `resolve_turn` not idempotent on resume (double-dock).** verification: a resume test
  (slice 7) re-enters the `SequentialDriver` loop after a recorded-but-unresolved CHALLENGE and
  asserts exactly one die is docked.
- **R6 — game-aware bounds breaking shared `create_match`/`arena`.** verification: bounds are
  enforced ONLY in route validators; a test (slice 9) creates a match via `arena.py`/the existing
  PD path with current bounds and asserts it still succeeds; a 3-player PD create via the platform
  route is still rejected.
- **R7 — leaderboard order disagreeing with elimination order.** verification: a test (slice 6/7)
  asserts `match_placement_key` ranks a finished LD match by elimination placement (winner top),
  not by PD's `(round_wins, total_score)` proxy.
- **R8 — malformed/partial `MatchState.config` bricking the loop.** verification: a test (slice 5)
  feeds a missing-key config and asserts the guarded accessor falls back to `config_defaults` and
  the match still runs.

## Out of scope (carried forward)

PD engine relocation; spot-on call; separate table-talk round; full dice animation; flipping LD
visible to non-admins. The public lobby (`app/routes/matches_user.py`) is untouched — LD is
`admin_only` and started from the admin create flow.
