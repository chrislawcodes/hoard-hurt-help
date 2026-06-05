# Architecture — Liar's Dice (game title #2)

This is a **map of the code we will add**: the modules, the data structures, and
the data flows for Liar's Dice on the Agent Ludum platform. It is the companion to
`specs/014-liars-dice/design.md` (the *why* and the product decisions) and follows
the same altitude as the repo-wide `ARCHITECTURE.md` (the *where*).

Read `design.md` first for the rules and the locked decisions (D-1…D-11). This doc
assumes them.

> **One-line summary:** Liar's Dice is a *sequential, hidden-information,
> elimination* game added behind the existing `GameModule` contract. PD is
> untouched. The platform grows in four additive seams — a sequential turn-loop
> mode, a private slice in the agent payload, a free-form move on the wire, and a
> generic per-title state store — each gated so simultaneous public games (PD)
> behave exactly as before.

---

## The core idea: a sequential, hidden game on a simultaneous platform

The platform today assumes every game is **simultaneous** (all players act each
turn, resolve at once) and **fully public** (everyone sees everything), running a
**fixed** rounds×turns grid. Liar's Dice breaks all three (design §1, §5):

| Liar's Dice needs | Platform default | Our additive seam |
|---|---|---|
| One player acts per turn, in seat order | All players act each turn | **Sequential loop mode** in the scheduler, gated by `GameConfig.simultaneous` (the flag already exists, unused) |
| Your dice are secret until a showdown | Whole history is public | **`your_private_state`** slice in the agent payload, built by the module |
| Move = bid (quantity+face) or challenge | Move = HOARD/HELP/HURT + target | **Free-form `move` dict** on the wire, passed through to the module |
| Hand ends on a challenge; match ends when one player is left | Fixed round/turn counts | **Module-driven loop hooks** (`next_actor`, `is_round_over`, `is_match_over`, `on_round_start`) |

**Design principle:** every seam is *additive and gated*. PD keeps the old code
path; Liar's Dice opts into the new one. The PD engine (`app/engine/*`) and the
stub-game conformance test stay green. This is the same "platform never imports a
game" rule the framework already enforces — we are only widening the contract.

---

## Modules

### 1. New game module — `app/games/liars_dice/`

The game itself. Self-contained; the only new file area that is Liar's-Dice-specific.

| Module | Responsibility |
|---|---|
| `game.py` | The `GameModule` implementation: config defaults (3–6 players, 5 dice, 30s, wild-on), `rules_text()` (reflects this match's wild on/off), `validate_move`, `record_submission`, `resolve_turn`, the new loop hooks, `finalize`, `final_placement`, `theme()`, registration. Thin — delegates the math to `engine.py`. |
| `engine.py` | **Pure** Liar's Dice rules. No DB. The heart: legal-raise check (incl. ace rules), challenge resolution (count face + wilds), smallest-legal-raise computation (missed-turn default), elimination + winner detection. Mirrors how PD keeps `app/engine/resolver.py` pure. Fully unit-testable. |
| `rules_text.py` | The plain-text ruleset sent to each AI, parameterized by wild on/off, dice count, and table size. |
| `strategy.py` | Strategy presets + default pre-fill prompt a human picks at join (like PD's `hoard_hurt_help/strategy.py`). |
| `sims.py` | The Liar's Dice Sim personalities (deterministic bidders/bluffers/challengers) — see §Sims below. May live under `app/engine/sims/` instead; placement is a tech-spec call. |

### 2. Platform extensions (additive, gated)

Touched platform files. Each change is guarded so PD's path is unchanged.

| Area | File(s) | Change |
|---|---|---|
| Turn loop | `app/engine/scheduler.py` | Add a **sequential mode**: when `GameConfig.simultaneous` is false, drive the hand by `next_actor` (open a single-actor turn) → resolve on that one submission → repeat until `next_actor` returns `None` (hand over) → `award_round` → `on_round_start`; end the match on `is_match_over`. Simultaneous mode = today's fixed grid. |
| Contract | `app/games/base.py` | Add the loop hooks (`next_actor`, `is_round_over`, `is_match_over`, `on_round_start`), the payload hooks (`private_state_for`, `public_state_for`), and `final_placement` — all with **default impls** that reproduce PD behavior. |
| Agent payload | `app/routes/agent_api.py`, `app/routes/agent_next_turn.py`, `app/schemas/agent.py` | "Your turn" only when you are the active actor; add `your_private_state` + a game-supplied `public_state` block; everyone else gets `waiting (not your turn)` carrying public state. |
| Wire format | `app/schemas/agent.py` (`SubmitRequest`) | Add optional free-form `move: dict`; keep PD's `action`/`target_id` for back-compat. Platform passes `move` through untouched (it already packs a `move` dict). |
| State storage | `app/models/` + a migration | Generic per-title state (see Data Structures): a `match_state` row and per-player private `player_state` rows, plus `quantity`/`face` columns on `turn_submissions` (D-3 rec). |
| Finish order | `app/engine/game_records.py`, the Elo reader (feature 013), `app/routes/web_viewer.py` | Read placement from `module.final_placement(...)` instead of assuming PD round-wins (design D-4). |

### 3. Sims — Liar's Dice computer players (D-9)

Deterministic, no-LLM players that bid/bluff/challenge sensibly in **both** wild
and no-wild modes, wired into the Practice Arena + auto-matches. They reuse the
existing Sim plumbing:

- `app/engine/sims/service.py` already auto-submits each Sim per phase from the
  scheduler. In sequential mode it auto-submits **only the active Sim's** move.
- `app/engine/arena.py` / `sim_presets.py` / `sims/seating.py` seed Sims into a
  match — reused as-is, with a Liar's-Dice Sim roster.
- The Sim decision logic calls the **same pure `engine.py`** the real game uses
  (probability of a bid being true given your own dice + total unknown dice). This
  shared core is the forcing function that keeps the engine honest.

### 4. Viewer — minimal v1 (TBD-6)

A text feed of bids + the showdown reveal + per-player dice-count bars, themed via
the module's `theme()`. Lives in `templates/fragments/` + `app/routes/web_viewer.py`
+ SSE. Fancy dice-table animation is deferred to the `game-art` skill.

---

## Data structures

### Persistent (database)

New, generic, and reusable by future hidden-info games — not Liar's-Dice-named.

**`match_state`** — one row per match; public game state the module owns.

```
match_state
  match_id        FK matches.id   (PK)
  state_json      JSON            # opaque to the platform; module reads/writes
```
For Liar's Dice `state_json` holds:
```json
{
  "hand": 6,
  "standing_bid": { "by": "P2", "quantity": 4, "face": 5 },
  "active_actor": "P3",
  "seat_order": ["P1","P2","P3","P4"],
  "wild_ones": true,
  "last_showdown": { "hand": 5, "bid": {...}, "actual_count": 4,
                     "loser": "P4", "revealed": { "P1":[...], ... } }
}
```

**`player_state`** — one row per (match, player); **private** per-player state.

```
player_state
  match_id        FK matches.id   ┐ PK
  player_id       FK players.id   ┘
  state_json      JSON            # NEVER exposed across players until reveal
```
For Liar's Dice:
```json
{ "dice": [5,5,1,3], "dice_count": 4, "eliminated": false }
```

**`turn_submissions`** (existing table, two columns added) — one row per move.

```
action            "BID" | "CHALLENGE"        (reuses the action column)
quantity          INT  NULL   ← new (D-3)    # bid quantity
face              INT  NULL   ← new (D-3)    # bid face
message           the public table-talk (D-5)
thinking          the private reasoning (D-5)
target_player_id  unused for Liar's Dice
points_delta      unused (dice, not points)
```

Storage we **do not** rebuild: `players` score columns are repurposed for display
only — `total_round_wins` = hands you won (challenges won), `total_round_score` =
placement points (`players − placement + 1`) so the existing leaderboard sort
stays meaningful (design §7).

### Logical / in-memory (the pure engine)

`engine.py` works on plain dataclasses, no DB — easy to test:

```
Bid        = { quantity: int, face: int }          # face 1..6
Move       = BidMove(quantity, face) | ChallengeMove()
Cup        = { dice: list[int] }                   # one player's hidden dice
TableView  = { dice_counts: {player_id: int},      # public
               standing_bid: Bid | None,
               wild_ones: bool }
Showdown   = { actual_count: int, bid: Bid, loser: player_id,
               revealed: {player_id: list[int]} }
```

Core pure functions (names provisional):
`is_legal_raise(prev, next, wild)`, `min_legal_raise(prev, total_dice, wild)`,
`count_for(face, all_dice, wild)`, `resolve_showdown(bid, all_cups, wild)`,
`winner(player_states)`.

### Wire shapes (Pydantic, `app/schemas/agent.py`)

**Submit** (extended, back-compat):
```json
{ "turn_token": "...",
  "move": { "type": "BID", "quantity": 5, "face": 5 },   // or {"type":"CHALLENGE"}
  "message": "I'm swimming in fives, P1.",
  "thinking": "Actually I have two." }
```

**Your-turn payload** (extended): the existing `static` + `scoreboard` +
`current`, with PD's public `history`/`summary` replaced for this game by:
```json
{ "your_private_state": { "dice": [5,5,1,3], "dice_count": 4 },   // you only
  "public_state": {
    "hand": 6, "standing_bid": {"by":"P2","quantity":4,"face":5},
    "active_actor": "P3", "wild_ones": true,
    "dice_counts": {"P1":3,"P2":2,"P3":4,"P4":1},
    "bid_history": [ {"by":"P1","quantity":3,"face":4,"message":"..."}, ... ],
    "showdowns": [ {"hand":5,"actual_count":4,"loser":"P4","revealed":{...}} ]
  } }
```
Non-active players get `WaitingResponse(reason="not_your_turn")` carrying the same
`public_state` so their AI can plan ahead.

---

## Data flows

### A. One sequential turn — a bid (server + agent)

```
scheduler                         module                     agent
   │ next_actor(match) ──────────▶ "P3"
   │ _open_turn (single-actor, token, 30s deadline)
   │ broadcast turn_opened ─────────────────────────────────▶ (SSE viewer)
   │                                            poll /turn ◀── P3
   │                            build payload  ─────────────▶ your_turn + your_private_state
   │                                                          (others: waiting/not_your_turn)
   │                                            submit move ◀─ {BID 5x5, message,...}
   │ validate_move(move) ────────▶ legal? (strictly-higher + ace rules)
   │ record_submission ──────────▶ write TurnSubmission(BID,5,5) + update match_state(standing=5x5, active→P4)
   │ _wait_for_turn (quorum = the 1 actor) → returns on submit
   │ resolve_turn ───────────────▶ bid turn = no dice change; mark resolved_at
   │ broadcast turn_resolved ───────────────────────────────▶ (SSE viewer)
   └─ loop: next_actor → "P4" ...
```
Missed deadline → platform asks `module.min_legal_raise(...)` and submits that as
the default (opening = min bid; ceiling = challenge) (D-11).

### B. A challenge ends the hand — showdown (server)

```
   │ active actor submits {CHALLENGE}
   │ record_submission ──────────▶ write TurnSubmission(CHALLENGE)
   │ next_actor(match) ──────────▶ None      # hand is over
   │ award_round ────────────────▶ resolve_showdown:
   │                                  reveal all cups (read player_state)
   │                                  count face + wilds across table
   │                                  bid true?  → challenger loses a die
   │                                  bid false? → bidder loses a die
   │                                  decrement loser's dice_count in player_state
   │                                  write last_showdown into match_state (reveal = public now)
   │ broadcast round_ended (showdown reveal) ────────────────▶ (SSE viewer)
   │ is_match_over(match) ───────▶ false → on_round_start: re-roll all still-in cups, clear standing_bid, loser leads
   └─ loop back to A for the next hand
```

### C. Match end + placement → records/Elo

```
   │ is_match_over(match) ───────▶ true (one player has dice)
   │ finalize ───────────────────▶ set winner_player_id (last standing)
   │                                write placement points + hands-won to players (display)
   │ final_placement(match) ─────▶ [winner, last-eliminated, ... , first-eliminated]
   │ broadcast game_completed ───────────────────────────────▶ (SSE viewer)
        │
   records/Elo (feature 013) reads final_placement (not round-wins) → pairwise Elo updates
```

### D. Hidden-information enforcement (the security flow)

Mirrors feature 007's "thinking" rule. One player's dice must never reach another
player's channels until the showdown reveals them.

```
player_state.dice
   ├─▶ your own /turn payload (your_private_state)      ✅ only you
   ├─▶ agent API / next-turn for other players          ❌ scrubbed (counts only)
   ├─▶ MCP tools                                         ❌ scrubbed
   ├─▶ spectator JSON API                                ❌ scrubbed (counts only)
   └─▶ AFTER a showdown: match_state.last_showdown.revealed → public everywhere ✅
```
A multi-channel leak test (like SC-002 in feature 007) asserts no pre-reveal dice
appear on any channel another player can read.

### E. Sims in the sequential loop

```
scheduler (sequential mode)
   │ next_actor ─▶ "P3"
   │ is P3 a Sim?  ── yes ─▶ sims.service.auto_submit_active(match, P3)
   │                              └─ sims.py decides via pure engine.py
   │                                 (truth-prob of standing bid given P3's cup + unknown dice)
   │                              └─ submit BID or CHALLENGE (+ a canned taunt for message)
   │ resolve as in flow A
```
Only the active Sim acts per turn (contrast PD, where every Sim acts each turn).

---

## Where to make a change (quick index)

| You want to… | Start here |
|---|---|
| Change a Liar's Dice rule (raise/ace/showdown) | `app/games/liars_dice/engine.py` (pure) |
| Change the move shape / validation | `engine.py` + `game.py:validate_move` + `SubmitRequest` |
| Change what an actor sees | `game.py:private_state_for` / `public_state_for` + `agent_api.py` |
| Touch the sequential loop | `app/engine/scheduler.py` (sequential mode) + the loop hooks in `base.py` |
| Add/adjust a Liar's Dice Sim | `app/games/liars_dice/sims.py` (or `app/engine/sims/`) |
| Change the dice/bid storage | `match_state` / `player_state` models + migration |
| Change the viewer | `templates/fragments/` + `web_viewer.py` + SSE |
| Wire placement into Elo | `module.final_placement` + the feature-013 reader |

---

## Notable shapes & tensions

- **The `simultaneous` flag finally does something.** It already exists on
  `GameConfig` (unused). Sequential mode reads it — no new config concept.
- **The module renders its own payload sections.** PD's generic public history
  doesn't fit hidden-info games, so the contract gains `private_state_for` /
  `public_state_for`. This is the real widening of the contract; keep it small and
  JSON-shaped so game #3 reuses it.
- **State lives in generic JSON, not PD columns.** `match_state` / `player_state`
  are deliberately opaque to the platform — the deferred "per-title state storage"
  from `DESIGN.md` §11, done minimally.
- **Pure engine, DB-thin module.** Same split PD uses (`resolver.py` pure,
  `game.py` thin). The Sims share the pure engine, so a rules bug shows up in both
  real play and Sim play — one place to fix.
- **Sequential cost.** A hand is many short turns instead of one big simultaneous
  turn, so wall-clock and AI-call counts scale with table size — the 6-player cap
  (D-7) and 30s deadline (D-8) keep this in check.
