# Spec — Liar's Dice (Phase C: the game module)

**Slug:** `liars-dice-phase-c` · **Branch:** `factory/liars-dice-engine` · **Base:** `origin/main`

Authoritative requirements: `specs/014-liars-dice/{design.md, tech-spec.md, architecture.md}`
(decisions D-1…D-11). This spec restates them as the build contract for the Feature
Factory run and pins every change to a real file path verified against the current code.

## 1. Goal

Ship Liar's Dice as game title #2 on the Agent Ludum platform: a complete, registered
`GameModule` (`game_type = "liars-dice"`) that plays end-to-end — lobby → scheduled
start → sequential turn loop → spectator viewer → completed match in records/leaderboard.
Registered **`admin_only=True`** so it stays invisible to non-admins until explicitly flipped.

Platform Phases A and B are already merged to `main` and supply the seams this builds on
(verified in code):
- `GameModule`/`BaseGameModule` hooks with PD-reproducing defaults (`app/games/base.py`):
  `next_actor`, `on_round_start`, `is_match_over`, `default_move`, `private_state_for`,
  `public_state_for`, `final_placement`, `match_placement_key`.
- `GameConfig` with `simultaneous`, `admin_only`, `min_players`, `max_players`,
  `per_turn_deadline_seconds`, `total_rounds`, `turns_per_round`.
- `SequentialDriver` (`app/engine/turn_drivers.py`), selected when `config.simultaneous`
  is False; drives one actor per turn via `next_actor`; bots currently get
  `module.default_move` in `_drive_actor_turn`.
- Generic per-title state: `MatchState` / `PlayerState` (`app/models/game_state.py`) +
  `turn_submissions.quantity` / `.face` (migration `0033`).
- Free-form `move` dict on `SubmitRequest`; `your_private_state` / `public_state` on
  `YourTurnResponse` (`app/schemas/agent.py`); `validate_move` called with
  `your_agent_id = player.seat_name` (`app/engine/agent_play.py`).
- `admin_only` visibility gate: `app/games.is_admin_only` / `visible_types`,
  `app/routes/web_support._can_view_game`.

## 2. Scope

### In scope (the deliverables)

1. **Pure rules engine** — `app/games/liars_dice/engine.py` (no DB, no async; only
   `from app.games.base import GameError`). Per tech-spec §5: `Bid`, `BidMove`,
   `ChallengeMove`, `parse_move`, `count_for`, `resolve_showdown`, `is_legal_raise`,
   `min_legal_raise`, `roll`. Exhaustive unit tests.
2. **The module** — `app/games/liars_dice/game.py`: `class LiarsDice(BaseGameModule)`
   over the engine. All 8 mandatory protocol methods + the sequential/hidden overrides
   (`next_actor`, `on_round_start`, `is_match_over`, `default_move`, `private_state_for`,
   `public_state_for`, `final_placement`, `award_round`, `finalize`, `record_submission`,
   `resolve_turn`, `validate_move`, `rules_text`, `agent_base_prompt`, `move_effect`,
   `theme`). Registered in `app/games/__init__.py`.
3. **Sequential bots** (D-9) — real bid/bluff/challenge logic for the single active
   actor in both wild and no-wild modes, replacing the `default_move` stand-in the
   `SequentialDriver` records for bots today. Built on the shared pure engine.
4. **Admin create-match fields** — `game_type` selector + LD-only **wild on/off**
   (default on) and **dice per player** (default 5), stored into `MatchState.state_json["config"]`.
   Requires parameterizing the currently PD-hardcoded create route (`app/routes/admin_api.py`)
   **and relaxing the shared player-range schema** — see §9.
5. **Minimal viewer** — text feed of bids + showdown reveal + per-player dice-count bars
   (`templates/fragments/` + `app/routes/web_viewer.py`), themed via `theme()`. The
   spectator JSON path (`app/routes/spectator_api.py`) must also surface LD public state.

### Additional platform touches surfaced by the spec review (code-confirmed)

These are small, additive seam changes the spec review proved are required. PD stays
byte-identical through all of them:

- **Validation snapshot** — `validate_move(move, *, your_agent_id, all_agent_ids)` receives
  no game state today (`app/engine/agent_play.py`, `app/games/base.py`). Per tech-spec §7
  the submit path must attach a read-only LD state snapshot (standing bid, per-seat dice
  counts, active actor, total dice, wild flag) **into the `move` dict** before calling
  `validate_move`, so the validator stays pure. Pinned in §6.
- **Player-range schema** — `app/schemas/admin.py` hardcodes `min_players`/`max_players`
  to `6..10` (inherited by `app/routes/admin_api.py` and `app/routes/game_admin_api.py`),
  which forbids legal 3–5-player LD games. Relax the bound to allow `3..6`. §9.
- **Per-match config persistence** — `app/engine/match_creation.py` only creates the
  `Match` row; nothing initializes `MatchState`/`PlayerState`. The admin form's wild/dice
  choices must be persisted at create time so they survive the scheduled-start gap. §9.
- **Sequential bot seam** — `SequentialDriver._drive_actor_turn` records
  `module.default_move` for bot actors; `app/engine/sims/service.py` is wired only into the
  simultaneous scheduler. Add a module bot-decision hook the `SequentialDriver` calls for
  bots. §7.
- **Public-state plumbing for views** — `web_viewer.py` and `spectator_api.py` build from
  generic timeline/player reads and never call `public_state_for()`. The viewer + spectator
  JSON must source LD public state from the module. §5 (viewer) + §8 (SC-HD sweep).
- **Public action schema widening (round-2 finding)** — `HistoryAction`
  (`app/schemas/agent.py`), `TimelineAction` (`app/read_models/matches.py`),
  `SpectatorAction` and `SpectatorState` (`app/schemas/spectator.py`) carry no
  `quantity`/`face` and no `public_state` slot. Widen these public shapes (additively,
  nullable) so a bid's quantity/face and the LD public-state block have a structured home
  for the viewer/spectator. PD leaves them null. Pinned concretely in the **plan**.
- **All three admin create paths** — there are three (round-3 finding): the platform-admin
  JSON API (`app/routes/admin_api.py`, hardcoded to PD), the HTML web form
  (`app/routes/game_admin_web.py`, its own `3..20` validation), and the per-game JSON API
  (`app/routes/game_admin_api.py`, `/api/game-admin/{game}/matches`, which already takes a
  game param). All must carry the LD fields and must not admit a table size outside the
  module's `min_players..max_players`. §9.

### Out of scope (non-goals)

- Relocating PD's engine out of `app/engine/` (tracked in HHH tech spec).
- Spot-on / "calza" call, a separate per-hand table-talk round, full dice-table
  animation (design deferrals D-2, D-5-deferred, TBD-6-full).
- Flipping LD visible to non-admins. It ships `admin_only=True`.
- Any change to PD behavior. See the hard gate below.

## 3. Hard gate — PD parity

**PD behavior must be byte-identical after this feature.** The PD test suite and
`tests/test_stub_game.py` pass **unmodified**. PD opts out of every new behavior by
inheriting `BaseGameModule` defaults; LD overrides them. This is the merge gate — if a
PD test changes or fails, the change is wrong.

## 4. The rules we ship (from design §3, locked)

- 3–6 players, 5 dice each (per-match adjustable), private cups, 30s/turn default.
- A **bid** = (quantity, face) claim over all dice on the table. On your turn: **Bid**
  (strictly higher than the standing bid) or **Challenge** (Dudo). Hand opener must bid.
- **Strictly-higher**: greater quantity at any face, or same quantity at a strictly
  greater face.
- **Wild ones (D-1, on by default, per-match toggle):** 1s are wild (count as the bid
  face at showdown). Switching normal→aces needs `quantity ≥ ceil(prev_q / 2)`;
  aces→normal needs `quantity ≥ 2*ace_q + 1`; ace→ace / normal→normal is plain
  strictly-higher. Wild **off**: every die counts only as its own face; ace quantity
  rules do not apply.
- **Showdown:** reveal all dice; count the bid face plus all 1s when wild;
  `count ≥ quantity` → bid holds (challenger loses a die); else bidder loses a die.
  The die-loser opens the next hand (or their left if eliminated).
- **Elimination/win:** 0 dice = out; match ends when exactly one player has dice; that
  player wins. Always terminates (total dice strictly shrink).
- **Missed-turn default (D-11):** smallest legal raise; opening default = minimum
  opening bid `Bid(1, 2)`; at the ceiling (no legal raise) → Challenge.

## 5. Engine contract (tech-spec §5) — the testable core

`app/games/liars_dice/engine.py`, pure:

- `Bid(quantity:int, face:int)` frozen dataclass; `BidMove`, `ChallengeMove`; `Move` union.
- `parse_move(raw: dict) -> Move` — raise `GameError("MALFORMED_MOVE", …)` on bad shape/type.
- `count_for(face, all_dice, *, wild) -> int` — dice showing `face`, plus all 1s when
  `wild` and `face != 1` (don't double-count 1s).
- `resolve_showdown(bid, all_dice, *, wild) -> (bid_holds, actual_count)`;
  `bid_holds = actual_count >= bid.quantity` (count == quantity holds).
- `is_legal_raise(prev, nxt, *, wild) -> bool` — strictly-higher + ace rules above;
  `prev is None` ⇒ opening (any valid `Bid`, face 2..6, q ≥ 1).
- `min_legal_raise(prev, total_dice, *, wild) -> Bid | None` — smallest strictly-higher
  legal bid; `None` at the ceiling; `prev is None` ⇒ `Bid(1, 2)`.
- `roll(n, rng: random.Random) -> list[int]` — n dice 1..6, deterministic by seed.

**Two gotchas the prototype hit (must be tested):**
(a) `wild=False`: after `(q, 6)` the next bid is `(q+1, 1)`, not `(q+1, 2)`.
(b) ace ordering: aces at quantity k sort **between** normal `(k-1, 6)` and `(k, 2)`.

**Property test:** for every `(prev, total_dice, wild)`, if `min_legal_raise(...)` is not
`None`, then `is_legal_raise(prev, result, wild=wild)` is True.

## 6. The module (tech-spec §6) — over the engine

`class LiarsDice(BaseGameModule)`, `game_type = "liars-dice"`:

- `config_defaults()` → `GameConfig(total_rounds=64, turns_per_round=256,
  per_turn_deadline_seconds=30, min_players=3, max_players=6, simultaneous=False,
  admin_only=True)`. `total_rounds`/`turns_per_round` are safe caps; the
  `SequentialDriver` ignores them.
- Per-match config (wild on/off default on; dice=5) lives in
  `MatchState.state_json["config"]`, seeded by `on_round_start(round=1)` from the admin
  form, defaulted by `config_defaults` if untouched.
- `on_round_start` — roll each still-in player's dice into `PlayerState`, clear the
  standing bid, set the hand leader (hand 1: seat 0; later: last die-loser or their left).
- `next_actor` — `None` when a challenge is pending; else the `active_actor` from
  `MatchState`, skipping eliminated seats. Returns a **seat_name**.
- `validate_move(move, *, your_agent_id, all_agent_ids)` — pure; `parse_move` then:
  not-your-turn → `NOT_YOUR_TURN`; challenge with no standing bid → `NOTHING_TO_CHALLENGE`;
  illegal raise → `ILLEGAL_RAISE`; quantity > total dice → `BID_TOO_LARGE`; face∉1..6 →
  `BAD_FACE`. Keys are seat_names. **The validator is pure and has no DB access**, so the
  submit path (`app/engine/agent_play.py`) must merge a read-only LD snapshot into the
  `move` dict before calling `validate_move`: `standing_bid`, `dice_counts` (per seat),
  `active_actor`, `total_dice`, `wild`. These keys are stripped before `record_submission`
  persists the move. This is the §2 "validation snapshot" touch.
- `record_submission` — store `action="BID"|"CHALLENGE"` + `quantity`/`face`; on BID,
  update `MatchState` (standing bid, advance `active_actor`); on CHALLENGE, set the
  challenge-pending flag so `next_actor` returns `None`.
- `resolve_turn` — BID: mark resolved only. CHALLENGE: mark resolved; showdown happens
  in `award_round`.
- `award_round` — the showdown: read all `PlayerState` dice, `resolve_showdown`, dock one
  die from the loser, write `last_showdown` (revealed dice now public) into `MatchState`,
  update player display fields.
- `is_match_over` — True when exactly one player has `dice_count > 0`.
- `finalize` + `final_placement` — winner = last standing; placement = elimination order
  (winner first, then reverse elimination order).
- `match_placement_key` — **must be overridden** (round-4 finding): the leaderboard engine
  (`app/read_models/leaderboard.py`) calls it directly, so overriding only `final_placement`
  leaves LD ranked by PD's `(round_wins, total_score)` proxy. Return a key that orders by
  elimination placement (hands-won as a secondary signal) so leaderboard order matches the
  game's finish order.
- `default_move` — `min_legal_raise` of the standing bid; opening → `Bid(1,2)`; ceiling →
  `{"type": "CHALLENGE"}`.
- `private_state_for` → `{"dice": [...], "dice_count": n}` for that player only.
- `public_state_for` → standing bid + per-player dice **counts** + recent showdowns,
  **seat_name keyed** (shape in tech-spec §7.2). No hidden faces pre-showdown.
- `rules_text` — reflects this match's wild mode + the exact submit JSON.
- `agent_base_prompt` — the LD system prompt that makes the agent play the whole game.
- `move_effect(action)` → `(0, None)`. `theme()` → LD color identity.

## 7. Sequential bots (D-9)

**Seam (pinned):** add a module bot-decision hook (e.g. `bot_move(db, match, player)`)
that `SequentialDriver._drive_actor_turn` calls for bot actors **instead of**
`module.default_move`. The spec review confirmed `app/engine/sims/service.py` is wired only
into the simultaneous scheduler, so the sequential driver must own the bot seam. PD is
unaffected (its simultaneous driver never calls this hook); `BaseGameModule` provides a
default that falls back to `default_move` so other sequential games keep working. Bots
estimate P(standing bid holds) from their own dice +
count of unknown dice, raise when confident, challenge when the standing bid is
improbable, bluff occasionally per personality. **Must read `public_state.wild_ones` and
play both modes correctly.** Deterministic given a seed (for tests/Practice Arena).

## 8. Hidden-information enforcement (tech-spec §8)

A player's dice faces never reach another player's channel before the showdown.
`PlayerState.dice` appears **only** in that player's own `your_private_state`. Other
players see dice **counts** only — across the agent API, every MCP tool, and the
spectator JSON (`app/routes/spectator_api.py`, which must source LD public state from
`public_state_for()` rather than generic reads) — until a showdown writes
`MatchState.last_showdown.revealed`, which is public thereafter. **Test SC-HD:** drive to a
pre-showdown state, assert no other player's faces leak in any of those three channels;
after a showdown, assert the revealed dice do appear. (A *bid's* quantity/face is public by
design and lives in `turn_submissions`, never the dice — so the public `history` path does
not leak hidden info.)

## 9. Admin create-match (tech-spec §9)

Parameterize the create route (`app/routes/admin_api.py`, currently hardcoded
`game="hoard-hurt-help"`): add a `game_type` field, default `"hoard-hurt-help"` for
back-compat. Add LD-only form fields (shown when game = `liars-dice`): **wild ones**
(on/off, default on), **dice per player** (default 5). PD's create path stays byte-identical.

Two code-confirmed prerequisites the spec review surfaced:
- **Game-aware player bounds (not a blanket relax).** `app/schemas/admin.py` hardcodes
  `min_players`/`max_players` to `6..10` and `create_match()` enforces `1..20`. A blanket
  widen would let the PD route create unsupported 3-player games and still wouldn't cap LD
  at 6. Instead make the bound **game-aware**: validate the requested table size against the
  selected module's `config_defaults().min_players..max_players`. PD keeps its 6..100; LD
  gets 3..6. **Apply this at the create-request validation layer only** (the admin routes /
  request schemas) — **not** inside `app/engine/match_creation.py` or `app/engine/arena.py`,
  which are shared by automated/Practice-Arena creation and intentionally use looser bounds
  (arena creates HHH with `min_players=1`). Tightening the shared core would break those
  callers (round-4 finding).
- **Persist per-match config at creation.** `app/engine/match_creation.py` only creates the
  `Match` row; nothing initializes `MatchState`. Store the LD wild/dice choices at create
  time (create the `MatchState` row with `state_json["config"]`, or carry the fields on the
  `Match` and have `on_round_start(round=1)` seed `MatchState.config`). `config_defaults`
  supplies values if the form is untouched. Table size uses existing min/max; deadline uses
  the existing field.

## 10. Acceptance criteria

- **AC1** Engine per §5 with exhaustive unit tests, including the property test
  (every `min_legal_raise` result satisfies `is_legal_raise`) and both gotchas (a)/(b).
- **AC2** `LiarsDice` module registered `admin_only=True`, `simultaneous=False`,
  3–6 players, 30s; plays a seeded sequential match to completion via `SequentialDriver`
  (a driver-level test: variable hand/turn counts, winner = last standing).
- **AC3** Bots play real bid/bluff/challenge logic in **both** wild and no-wild modes
  (not `default_move`); deterministic by seed.
- **AC4** Minimal viewer renders bids + showdown reveal + dice-count bars for an LD match.
- **AC5** Admin create-match: `game_type` selector + wild on/off + dice-per-player,
  persisted to `MatchState.config`; an admin can create and start an LD match from the
  admin create flow. (LD is `admin_only=True`, so it is intentionally absent from the
  public lobby `app/routes/matches_user.py` for non-admins — that path is not in scope.)
- **AC6 (gate)** PD suite + `tests/test_stub_game.py` pass unmodified; full preflight
  green (`ruff`, `mypy app/ mcp_server/`, `pytest -q`).
- **AC7 (security)** SC-HD: no other player's dice faces in agent API + MCP + spectator
  JSON pre-showdown; revealed dice appear post-showdown.
- **AC8** `MatchState` / `PlayerState` JSON round-trips (dirty-tracking guard) — a test
  proves in-place edits persist.

## 11. Risks & assumptions

- **R1** Hidden-info leak via a forgotten channel (MCP `get_game_state`, spectator JSON).
  *verification:* SC-HD sweeps all three channels (AC7) and must be green pre-merge.
- **R2** PD regression from the admin-route parameterization or a shared payload path.
  *verification:* AC6 gate — PD suite + stub test unmodified and green; run full preflight
  on every slice.
- **R3** `MatchState`/`PlayerState` in-place JSON edits silently not persisting.
  *verification:* AC8 round-trip test (the classic `MutableDict` bug).
- **R4** Bot decision logic wedging the loop (e.g. illegal move) or being non-deterministic.
  *verification:* AC2/AC3 — a seeded bots-only match runs to completion; assert a fixed
  winner for a fixed seed.
- **R5** `resolve_turn` not idempotent under a mid-hand restart (Gemini): the
  `SequentialDriver` resumes from `MatchState`, so re-resolving a CHALLENGE could double-dock
  a die. *verification:* a resume test that re-enters the loop after a recorded-but-unresolved
  CHALLENGE asserts exactly one die is docked.
- **R6** Malformed/partial `MatchState.config` bricking the loop (Gemini): a bad JSON blob
  makes `next_actor`/`resolve_turn` raise. *verification:* the module reads config through a
  guarded accessor that falls back to `config_defaults`; a test feeds a missing-key config
  and asserts the match still runs.
- **Assumption** `specs/014-liars-dice/` is correct/current; Phases A/B seams are as the
  live seam map shows; PR 371 (Direct engine) is not consulted.

## 12. Spec-review reconciliation (Feature Factory)

The spec checkpoint (Codex feasibility + Gemini requirements) raised real, code-confirmed
gaps. Accepted and folded into this spec:
- **Accepted (Codex):** validation snapshot (§6), player-range schema relax (§9),
  per-match config persistence (§9), pinned sequential bot seam (§7), viewer/spectator
  `public_state_for` plumbing (§5/§8). All listed under §2 "Additional platform touches."
- **Accepted (Gemini):** `resolve_turn` resume idempotency (R5), guarded config read (R6),
  and the clarification that public bid history does not leak hidden dice (§8).
- **Accepted (round 3):** a third create path (`game_admin_api.py`) and game-aware player
  bounds instead of a blanket schema relax — both folded into §2/§9.
- **Carried to the plan (design-level):** route LD view rendering through the module/theme
  rather than growing a `game_type` switch in `web_viewer._game_view_context`; use an atomic
  write pattern for `MatchState`/`PlayerState` (reassign or rely on `MutableDict` + explicit
  commit, guarded by the AC8 round-trip test); unify the bot seam so `BaseGameModule.bot_move`
  defaults to `default_move`.
- **Noted, not changed:** Gemini's polling-interval saturation and generic concurrency
  risks — the platform is single-writer per match (no concurrent submitters for a
  single-actor turn), so these are pre-existing platform characteristics, not LD-introduced.
