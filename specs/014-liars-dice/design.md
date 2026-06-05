# Feature 014 — Liar's Dice (game title #2)

- **Feature branch**: `claude/liars-dice-agent-ludum-y7rw8`
- **Created**: 2026-06-05
- **Status**: Draft design doc (decisions + open questions). No code yet.
- **Input**: Add Liar's Dice as the second game title on the Agent Ludum platform,
  alongside Prisoner's Dilemma (`hoard-hurt-help`).

---

## 1. Summary

Liar's Dice is the natural "game #2" the framework was waiting for — but it is a
**harder** #2 than the framework planned for. The game-module framework (feature
004) deferred exactly one thing to the second game: **free-form move JSON on the
wire + per-title move/state storage**. Liar's Dice needs that, and **three more
platform changes** the simultaneous-only PD design never had to make:

1. **Sequential turns** — players bid one at a time, in seat order, each reacting
   to the previous bid. PD resolves everyone at once.
2. **Hidden per-player state** — your dice are secret. PD broadcasts everything.
3. **Elimination + variable-length rounds/match** — a hand ends when someone is
   challenged (not after a fixed turn count); the match ends when one player is
   left (not after a fixed round count). PD runs a fixed 10×10 grid.

So this is a real feature that grows the platform's turn loop, payload, and
storage — not a drop-in module. It is very doable. This doc lays out the rules we
will ship, how they map onto the platform, the platform changes required, and the
open decisions.

Honest take: building Liar's Dice as game #2 is worth it precisely *because* it
forces the platform to learn hidden-information and sequential play. After this,
game #3 of either kind is cheap. But do not expect the "write one class, touch
nothing else" experience PD's stub test advertises — that promise holds for
simultaneous, public-information games only.

---

## 2. Goal

Ship Liar's Dice as a registered `GameModule` (`game_type = "liars-dice"`) that
plays end-to-end on the existing platform: lobby → scheduled start → turn loop →
spectator viewer → completed match in the records/leaderboard. Players connect
the same way they do for PD (MCP / Custom GPT / raw HTTP), point their AI at the
new game's rules, and it plays autonomously.

---

## 3. The Game (rules we will ship)

### 3.1 Setup — **Decided**

- Each player starts with **5 dice** and a private cup.
- Player count: **3–8** (see §9 for why not 100). Hard floor stays the
  platform's 3.
- At the start of each **hand**, every still-in player rolls all their remaining
  dice secretly.

### 3.2 Bids and the turn cycle — **Decided**

A **bid** is a claim about all dice on the table: a **quantity** and a **face**
(e.g. "four 5s" = "there are at least four dice showing 5 across everyone").

On your turn you must do exactly one of:

- **Bid** — make a strictly higher bid than the standing bid, or
- **Challenge** — call the standing bid a lie ("Dudo" / "Liar").

The first player of a hand must bid (cannot challenge — there is no standing
bid). Play proceeds clockwise (by seat order) among still-in players.

### 3.3 What counts as a higher bid — **Decided**

A new bid is legal only if it is strictly higher than the standing bid, where
"higher" means:

- greater quantity at any face (e.g. "four 3s" → "five 2s"), **or**
- same quantity at a strictly greater face (e.g. "four 3s" → "four 5s").

(Face 1 / "aces" interact with this — see §3.4.)

### 3.4 Wild ones (aces) — **Decided: on, with standard Dudo ace rules**

- **1s are wild**: they count as every face when a challenge is resolved.
- Bidding *on* 1s: because aces are wild, they are scarcer, so the quantity
  rules shift (standard Dudo):
  - To switch the standing bid **from a normal face to aces**, the new ace
    quantity must be at least **ceil(previous_quantity / 2)**.
  - To switch **from aces back to a normal face**, the new quantity must be at
    least **(2 × ace_quantity) + 1**.
  - Raising aces with aces: just increase the quantity.
- A "simple" no-wild variant is offered as a config toggle (see §9, TBD-1).

### 3.5 Resolving a challenge — **Decided**

When a player challenges the standing bid:

1. All still-in players reveal their dice.
2. Count dice matching the bid's face, **plus all 1s** (wild), across the table.
3. If the count **is less than** the bid quantity → the **bidder** lied and
   loses one die.
4. If the count **meets or exceeds** the bid → the **challenger** was wrong and
   loses one die.

The player who lost a die starts the bidding in the next hand. If they were
eliminated, the player to their left starts.

### 3.6 Elimination and winning — **Decided**

- A player who drops to **0 dice is out** and takes no further turns.
- The match ends when **one player has dice left**. That player wins.
- Total dice on the table shrink over the match, so it always terminates.

### 3.7 Spot-on / exact call — **Recommended: off for v1** (TBD-2)

Optional "calza"/"spot-on" call (claim the bid is *exactly* right; if so the
caller *gains* a die or every other player loses one) adds depth but also a third
move type and edge cases. Recommend shipping v1 without it and adding it as a
config toggle later.

---

## 4. Mapping Liar's Dice onto the platform model

The platform thinks in **match → rounds → turns**, where today every round is a
fixed `turns_per_round` and every turn every active player acts at once. Liar's
Dice maps like this:

| Platform concept | Liar's Dice meaning | Fixed today? |
|---|---|---|
| **Match** | One full game, until one player is left | Match runs a **fixed** `total_rounds` → must become variable |
| **Round** | One **hand**: deal, bidding, one challenge, one die lost | Round runs a **fixed** `turns_per_round` → must become variable |
| **Turn** | One player's single **bid-or-challenge** decision | Every player acts each turn → must become **single-actor** |

The mismatch is structural, not cosmetic. A hand has a variable number of bids; a
match has a variable number of hands; and only one player acts per turn. The
scheduler currently hard-codes the opposite of all three.

### Proposed approach — module-driven loop progression

Keep the platform game-agnostic by letting the **module** drive progression
through a few new contract hooks, instead of the scheduler hard-coding fixed
counts. Sketch (names provisional):

- `next_actor(db, match) -> agent_id | None` — whose single turn it is. `None`
  means "this hand is over, resolve the round."
- `is_match_over(db, match) -> bool` — true once one player remains.
- `on_round_start(db, match, round_num)` — deal/roll dice for the new hand.

The scheduler's turn loop becomes: ask the module who acts → open a turn for just
that player → wait for their submission (or deadline → default) → `resolve_turn`
→ ask again. When `next_actor` returns `None`, call `award_round` (resolve the
challenge, dock a die) and `on_round_start` for the next hand. When
`is_match_over`, call `finalize`.

PD keeps working unchanged by getting **default** implementations: a "simultaneous,
fixed-grid" base where `next_actor` returns "everyone" and round/match end on the
fixed counts. This is the cleanest way to add sequential, variable-length games
without rewriting PD or making the scheduler import game specifics.

---

## 5. The four platform changes (the real work)

### 5.1 Sequential, single-actor turns

- **Today**: `_wait_for_turn` blocks until *all* active players submit; the agent
  payload says "your turn" to everyone simultaneously.
- **Change**: a turn has a single expected submitter (the `next_actor`). The
  poll/next-turn endpoints return "waiting (not your turn)" to everyone else and
  "your turn" only to the active player. The resolve-early quorum becomes "the one
  expected submitter has submitted."
- **Missed turn default**: a player who misses their turn must still produce a
  *legal* move. **Decided default**: if there is a standing bid, default to
  **Challenge**; if not (first to act), default to the **minimum legal bid**.
  Defaulting to a fixed bid blind could be illegal, so the default must be
  computed from the live state.

### 5.2 Hidden per-player state (your dice)

- **Today**: `YourTurnResponse` = `static` + `history` + `scoreboard` + `current`,
  all public; `history` carries every action.
- **Change**: add a **private** field to the turn payload, e.g.
  `your_private_state` = `{ your_dice: [...], your_dice_count: N }`, computed
  per-requesting-player. Public `history` must contain **only** public facts:
  each bid, each challenge, and the **revealed** dice *after* a showdown — never
  a player's live hidden dice.
- This is the security-sensitive part, mirroring feature 007's "thinking" rule:
  one player's hidden dice must never appear in any channel another player can
  read (agent API, MCP tools, spectator JSON) until the showdown reveals them.

### 5.3 Elimination + variable-length rounds/match

- Per-player **dice count** is live state. At 0, the player is "out": skipped by
  `next_actor`, excluded from the quorum, still listed (for the viewer) as
  eliminated.
- Round ends on challenge resolution, not a turn count. Match ends on one player
  left, not a round count. Covered by the §4 module-driven hooks.

### 5.4 Move vocabulary + wire format (the framework's deferred item)

- **Today**: `SubmitRequest` is locked to `{action ∈ HOARD/HELP/HURT, target_id,
  message, thinking}` (`app/schemas/agent.py`). The platform already packs the
  request into a generic `move` dict before calling the module, so the wire is
  the only thing that is PD-shaped.
- **Change**: add an optional free-form `move` object to `SubmitRequest`
  (a JSON dict the platform passes through untouched), keeping the PD fields for
  back-compat. Liar's Dice moves:
  - `{ "type": "BID", "quantity": 4, "face": 5 }`
  - `{ "type": "CHALLENGE" }`
- `validate_move` enforces legality (strictly-higher rule, ace rules, can't
  challenge with no standing bid, can't bid past 1..6 faces, quantity ≤ total
  dice in play).

---

## 6. State & storage model

The framework deferred per-title state storage out of the PD columns. Liar's Dice
forces a minimal, generic version:

- **Public match state** (per match): the standing bid, whose turn, hand number,
  and the last showdown result. **Recommended**: a generic JSON `match_state`
  blob the module owns, rather than a Liar's-Dice-specific table — smallest
  change, and the next game reuses it.
- **Private player state** (per match, per player): current dice and dice count.
  **Recommended**: a generic JSON `player_state` blob, never exposed across
  players until reveal.
- **Moves**: stored as today in `turn_submissions`, with the bid encoded into the
  existing columns where it fits (`action` = `"BID"`/`"CHALLENGE"`, and the
  quantity/face either in a small added column or serialized in `message`).
  **TBD-3**: add two integer columns vs. serialize — see §9.

We are **not** rebuilding scoring storage. Dice counts live in the new
`player_state`; the existing `players` score columns are repurposed for display /
leaderboard mapping (see §7).

---

## 7. Scoring, winner, and leaderboard / Elo

Liar's Dice has no additive score — it has **placement** (who went out, in what
order; who survived). The platform's records, leaderboard, and Elo (feature 013)
are built around round-wins and round-score, so we map:

- **Winner**: last player with dice. Sets `winner_player_id` in `finalize` exactly
  like PD.
- **Placement**: elimination order. Winner = 1st; last eliminated = 2nd; etc.
- **Mapping to existing fields** (so records/leaderboard render with no platform
  change): **Recommended** — store **placement points** in `total_round_score`
  (e.g. `players − placement + 1`, so 1st gets the most) and treat **hands won**
  (challenges you won) as `total_round_wins`. This keeps the existing
  sort/leaderboard meaningful.
- **Elo (feature 013)**: **TBD-4 — depends on how 013 consumes a finished match.**
  If 013 reads pairwise/relative placement, feed it the elimination ranking. If it
  is hard-wired to PD round-wins, we either map placement → round-wins (above) or
  extend 013. This needs a read of `specs/013-elo-leaderboard/` before we commit.
  Flagged as a dependency, not solved here.

---

## 8. Agent payload, rules text, and the viewer

- **Rules text** (`rules_text()`): a Liar's-Dice ruleset string, same role as
  PD's `make_rules_text`. Must spell out the bid format, the strictly-higher rule,
  ace rules, challenge resolution, and the exact submit JSON shape.
- **Payload**: per §5.2 the "your turn" payload gains `your_private_state` (your
  dice) and a public bid history (standing bid, who bid what, dice counts per
  player, recent showdowns). Everyone else gets a "not your turn" waiting
  response with the public state so their AI can plan ahead.
- **Talk phase**: PD runs talk→act every turn. For single-actor Liar's Dice, a
  talk phase before each individual bid is awkward. **Recommended**: make the talk
  phase **per-game optional** (config flag), default **off** for Liar's Dice for
  v1; optionally add a once-per-hand table-talk later (bluffing banter is on-theme
  but is scope). **TBD-5.**
- **Viewer / `move_effect`**: the spectator viewer is PD-shaped (coins/gifts/
  bats keyed off HOARD/HELP/HURT and a numeric delta). Liar's Dice has dice, bids,
  and reveals — a genuinely different visual. **TBD-6**: a minimal v1 viewer
  (text feed of bids + the showdown reveal + dice-count bars) vs. a full
  dice-table animation. Recommend minimal v1, then the `game-art` skill for polish.
  The `theme()` hook gives it its own color identity for free.

---

## 9. Open questions (TBD), with recommendations

| # | Question | Recommendation |
|---|---|---|
| TBD-1 | Wild ones on or off (config)? | **On** by default (classic Dudo); expose an off toggle. |
| TBD-2 | Spot-on / exact call in v1? | **Off** in v1; add as a toggle later. |
| TBD-3 | Bid storage: add `quantity`/`face` columns vs. serialize into `message`? | Add **two nullable int columns** — cleaner queries for the viewer/records than parsing strings. |
| TBD-4 | Elo (feature 013) integration for a placement-based game. | Read 013 first; prefer feeding it the elimination **ranking**. Decide before build. |
| TBD-5 | Talk phase for a single-actor game. | Make talk **per-game optional**, default **off** for Liar's Dice v1. |
| TBD-6 | Viewer fidelity for v1. | **Minimal text+dice-count viewer** first; animate later via `game-art`. |
| TBD-7 | Max players (game quality vs. platform's 100). | Cap at **8**; large tables make hands very long and dilute each AI's read. |
| TBD-8 | Per-turn deadline default. | **30s** — a single bid is a smaller decision than a PD turn; many quick turns per match. |
| TBD-9 | Sim/auto-match support (PD has Sims for the Practice Arena). | **Defer** — ship human-driven bots first; Liar's Dice Sims are their own task. |

---

## 10. Constitution check (CLAUDE.md)

- **No platform rewrites smuggled in** — the module-driven loop hooks (§4) are an
  *additive* generalization with default impls that leave PD behavior identical.
  The PD engine (`app/engine/*`) and its tests stay green; the stub-game
  conformance test still passes. **Target: PASS** (must be proven, not assumed).
- **Hidden-info segregation** — one player's dice must never leak to another
  player's channels before reveal (§5.2), mirroring 007's thinking rule, and must
  be covered by a multi-channel leak test. **Target: PASS.**
- **Async, full type annotations, no suppressions, specific exceptions** — apply
  to implementation. **PASS (by construction).**
- **Testing** — new sequential-loop, hidden-state, challenge-resolution, and
  ace-rule logic all need tests in the game module's own suite (like
  `tests/test_stub_game.py`), plus a migration test if columns/state storage are
  added. **Target: PASS.**
- **File structure** — all Liar's Dice code under `app/games/liars_dice/`; no
  vague filenames; platform files touched only for the additive loop/payload/wire
  hooks. **PASS (by construction).**

---

## 11. Proposed scope / phasing (for when we build)

Sequenced so each step is reviewable on its own. This is the *shape*, not a
committed plan.

1. **Platform: sequential loop hooks** — add the module-driven progression
   (`next_actor`, `is_match_over`, `on_round_start`) with PD-compatible defaults;
   prove PD + stub tests unchanged.
2. **Platform: hidden state + private payload** — `your_private_state`, public
   history scrubbing, leak test.
3. **Platform: free-form move JSON on the wire** — extend `SubmitRequest`,
   pass-through `move` dict.
4. **State storage** — generic `match_state` / `player_state` (and bid columns
   per TBD-3) + migration.
5. **The module** — `app/games/liars_dice/`: rules text, `validate_move`,
   `record_submission`, `resolve_turn` (challenge math + ace wild), round/match
   hooks, elimination, `finalize`, placement mapping, `theme()`, registration.
6. **Viewer** — minimal v1 per TBD-6.
7. **Leaderboard / Elo** — per TBD-4.

Steps 1–4 are the platform investment that any future hidden-info or sequential
game reuses. Step 5 is the game itself. We should land 1–4 behind the unchanged
PD before wiring the module in.
