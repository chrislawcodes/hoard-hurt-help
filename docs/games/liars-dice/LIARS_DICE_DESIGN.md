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
   left (not after a fixed round count). PD runs a fixed 7×7 grid (49 turns).

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

- Each player starts with **5 dice** and a private cup. (Adjustable per match;
  5 is the default — see §9.)
- Player count: **3–6** (see §9 for why not 100). This game sets a minimum floor
  of 3 players.
- Default per-turn deadline: **30 seconds** (adjustable per match) — a single
  bid is a quick decision and there are many turns per match.
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

### 3.4 Wild ones (aces) — **Decided: on by default, match-creator toggle**

Wild ones are **on by default**, but the match creator can turn them off when
creating a match (a config flag, like the other match settings). Two
consequences: the `rules_text()` sent to each AI must state which mode *this*
match uses, and the Bots (§9, TBD-9) must play both modes correctly.

When wild ones are **on** (standard Dudo ace rules):

- **1s are wild**: they count as every face when a challenge is resolved.
- Bidding *on* 1s: because aces are wild, they are scarcer, so the quantity
  rules shift (standard Dudo):
  - To switch the standing bid **from a normal face to aces**, the new ace
    quantity must be at least **ceil(previous_quantity / 2)**.
  - To switch **from aces back to a normal face**, the new quantity must be at
    least **(2 × ace_quantity) + 1**.
  - Raising aces with aces: just increase the quantity.

When wild ones are **off**, every die counts only as its own face and the special
ace-bidding quantities above do not apply. (When wild ones are off, also see
§3.5 — only the bid's face counts when a challenge is resolved, not 1s.)

### 3.5 Resolving a challenge — **Decided**

When a player challenges the standing bid:

1. All still-in players reveal their dice.
2. Count dice matching the bid's face, **plus all 1s** (wild, if wild ones are on)
   across the table.
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

### 3.7 Spot-on / exact call — **Decided: off for v1**

The optional "calza"/"spot-on" call (claim the bid is *exactly* right; if so the
caller *gains* a die or every other player loses one) adds depth but also a third
move type and edge cases. v1 ships with just **bid** and **challenge**; spot-on
is a later config toggle.

---

## 4. Mapping Liar's Dice onto the platform model

The platform thinks in **match → rounds → turns**. The default PD config runs
7 rounds of 7 turns each (49 turns total, 7×7 grid); every turn every active
player acts at once. Liar's Dice maps like this:

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
- **Missed turn default — Decided: smallest legal raise.** A player who misses
  their turn must still produce a *legal* move, computed from live state. The
  default is the **lowest legal bid above the standing bid** ("do no harm" — keeps
  the no-show in the hand, leaks nothing, costs them nothing directly). Two
  fallbacks: the first player of a hand (no standing bid) defaults to the
  **minimum opening bid**; and when the bid is already at the ceiling (no legal
  raise exists), the default is **Challenge**.

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
- **Elo (feature 013) — good fit, one small change.** 013 rates matches from
  **final placement** ("Normal Multiplayer Elo from final match placement";
  "final placement is converted into pairwise comparisons"). Liar's Dice produces
  a placement (elimination order) natively, so it fits 013's model cleanly — no
  Elo rework. The one change: 013 currently *derives* placement from PD round-wins,
  so the platform must let each game report its own finish order (elimination order
  for Liar's Dice) instead of assuming round-wins. That's a small, clean platform
  edit, not an Elo redesign. The "First-place Bonus" variant also just works, since
  the winner is unambiguous (last player standing).

---

## 8. Agent payload, rules text, and the viewer

- **Rules text** (`rules_text()`): a Liar's-Dice ruleset string, same role as
  PD's `make_rules_text`. Must spell out the bid format, the strictly-higher rule,
  ace rules, challenge resolution, and the exact submit JSON shape.
- **Payload**: per §5.2 the "your turn" payload gains `your_private_state` (your
  dice) and a public bid history (standing bid, who bid what, dice counts per
  player, recent showdowns). Everyone else gets a "not your turn" waiting
  response with the public state so their AI can plan ahead.
- **Viewer / `move_effect`**: the spectator viewer is PD-shaped (coins/gifts/
  bats keyed off HOARD/HELP/HURT and a numeric delta). Liar's Dice has dice, bids,
  and reveals — a genuinely different visual. **TBD-6**: a minimal v1 viewer
  (text feed of bids + the showdown reveal + dice-count bars) vs. a full
  dice-table animation. Recommend minimal v1, then the `game-art` skill for polish.
  The `theme()` hook gives it its own color identity for free.

### 8.1 Communication / table talk — **Decided: a message rides with each bid**

Table talk is close to the *point* of Liar's Dice. It is a bluffing game: claiming
dice you don't have, baiting a challenge, talking an opponent into a bad call. For
this platform that is also the richest research signal — the gap between what an
agent *says* about its dice and what it actually holds is the same "say one thing,
do another" gap feature 007's `thinking` field already exposes. So talk is not a
nice-to-have here; it is core.

The platform already runs each turn as a **talk phase then an act phase** (feature
007: a public `talk`/message via a `record_message` hook + a TurnMessage table,
then the "act" submission with the move). We can either **fold the message into the
act submission** (single round-trip per turn, but the message rides with the move)
or **reuse the existing talk-phase hook** (two round-trips, separate call per turn,
but cleanly separated). The decision is:

- **Decided**: fold the message into the act submission. The acting player attaches
  an optional **public message** (and an optional private `thinking`) to its
  bid/challenge — same `message`/`thinking` fields PD already carries, just on the
  one actor whose turn it is. The viewer shows e.g. *"P3 bids five 5s — 'I'm
  swimming in fives, P1.'"* with reasoning behind a per-bot toggle, exactly like
  007.
- **Note on the limit**: only the active player can speak each turn; others can't
  reply until their own turn. A free, everyone-can-chatter channel would model a
  real table more closely but adds a phase and cross-talk complexity.
- **Deferred option**: a once-per-hand table-talk round (everyone broadcasts
  before bidding opens) if we later want alliance/cross-talk dynamics. Costs ~1
  extra model call per player per hand.

The bid itself is *already* a bluff channel (a bid either reflects your hand or
misrepresents it), so deception exists even with talk off. The message adds the
directed taunt/persuasion layer on top.

---

## 9. Decisions and remaining open questions

Decisions made with Chris on 2026-06-05:

| # | Question | **Decision** |
|---|---|---|
| D-1 | Wild ones on or off? | **On by default**, with a **match-creator toggle** to turn off. Rules text states the mode; Bots play both. (§3.4) |
| D-2 | Spot-on / exact call in v1? | **Off** in v1 (bid + challenge only); later toggle. (§3.7) |
| D-4 | Elo (feature 013) integration. | **Good fit** — 013 rates by final placement, which Liar's Dice gives natively. One small platform edit: let each game report its own finish order (elimination order) instead of assuming round-wins. (§7) |
| D-5 | Table talk for a single-actor game. | A public **message + thinking ride with each bid/challenge** by default (no separate talk phase). Per-hand table-talk round deferred. (§8.1) |
| D-7 | Max players. | **Cap at 6** (floor stays 3). Short, readable hands; watchable match length. (§3.1) |
| D-8 | Per-turn deadline default. | **30s**, adjustable per match. (§3.1) |
| D-9 | Bots / auto-match support. | **Build Bots in v1** — Liar's Dice gets a Practice Arena and auto-matches from day one. Adds a real work stream: Bots that bid, bluff, and challenge sensibly in both wild/no-wild modes. (§11) |
| D-10 | Dice per player. | **5 each** by default, adjustable per match. (§3.1) |
| D-11 | Missed-turn default. | **Smallest legal raise**; opening default = minimum opening bid; ceiling fallback = challenge. (§5.1) |

Still open:

| # | Question | Recommendation |
|---|---|---|
| TBD-3 | Bid storage: add `quantity`/`face` columns vs. serialize into `message`? | Add **two nullable int columns** — cleaner queries for the viewer/records than parsing strings. |
| TBD-6 | Viewer fidelity for v1. | **Minimal text + dice-count viewer** first; animate later via `game-art`. |

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

## 11. Phasing — decoupling first, then the game

Three phases, **each its own branch + PR**, landed in order. The point of the
ordering is **cause isolation**: a phase boundary that goes green→broken tells you
which layer regressed. The decoupling refactor lands and is proven against PD
*before* any Liar's Dice behavior exists, so early breakage is unambiguously the
refactor, not the new game. This also honors CLAUDE.md's one-feature-per-branch
rule — the decoupling is a separate feature from the game.

The split rests on one distinction: some "decoupling" is a **pure parity
refactor** (provable with PD alone), and some is **new capability** (nothing
exercises it until a game does — building it blind means guessing the shape).
We separate the two.

### Phase A — PD parity refactor (its own PR, merged first)

The work in `LIARS_DICE_DECOUPLING_TECH_SPEC.md`. No new behavior:

- Extract `SimultaneousDriver` from the scheduler (move PD's loop, don't rewrite).
- Add the new contract hooks with **PD-reproducing defaults** (`is_match_over`,
  `final_placement`, `default_move`, `private_state_for`, `public_state_for`,
  `on_round_start`).
- Route PD's payload through `public_state_for` / `private_state_for` (same bytes).
- Free-form `move` passthrough on the wire (PD ignores it).
- Records/Elo read `final_placement` instead of assuming round-wins (D-4).
- Additive migration: `match_state` / `player_state` tables + `quantity`/`face`
  columns (PD writes none of them).

**Gate:** parity (SC-P1…SC-P5 in `LIARS_DICE_DECOUPLING_TECH_SPEC.md`). PD suite +
the existing stub test green, unmodified. If anything breaks, it is here.

### Phase B — new seams, validated by a stub (its own PR)

Build the capability PD never needed, and prove it with a **minimal sequential,
hidden-information stub game** (extend `tests/test_stub_game.py`) — cheap and
throwaway, so a seam bug fails the stub, not the real game:

- `SequentialDriver` (single-actor turn loop, `next_actor`-driven).
- Real private/public payload split + the multi-channel hidden-info leak test.
- The generic `match_state` / `player_state` store actually read and written.

**Gate:** the stub plays a full sequential, hidden-state match to completion; the
leak test passes; PD parity from Phase A still green.

### Phase C — Liar's Dice itself (its own PR)

By now the platform is proven, so a failure here is a *game-logic* bug:

1. **Pure engine** — `app/games/liars_dice/engine.py`: legal-raise + ace rules,
   showdown count (face + wilds), `min_legal_raise` (missed-turn default),
   elimination/winner. Unit-tested in isolation.
2. **The module** — `game.py`: rules text (per-match wild on/off), `validate_move`,
   config defaults (3–6 players, 5 dice, 30s, wild on), `record_submission`,
   `resolve_turn`, the loop hooks, `award_round` showdown, `finalize`,
   `final_placement` (elimination order), `theme()`, registration.
3. **Bots** (D-9) — Liar's Dice players that bid/bluff/challenge in both wild and
   no-wild modes, on the shared pure engine; wired into the Practice Arena +
   auto-matches.
4. **Viewer** — minimal v1 per TBD-6.
5. **Admin create-match fields** — wild on/off, dice count.

Phase C can itself be reviewed in slices (engine → module → Bots → viewer), but it
is one feature/branch.
