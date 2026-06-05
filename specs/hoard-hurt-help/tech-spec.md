# Tech Spec — Hoard-Hurt-Help decoupling (PD side of hosting game #2)

The PD-side work needed so the platform can host Liar's Dice **without changing
how PD plays**. This is a refactor spec: its headline deliverable is *zero
behavior change*. Read alongside `specs/014-liars-dice/tech-spec.md` (the platform
seams) and `specs/hoard-hurt-help/architecture.md`.

Standards and preflight: same as the repo (CLAUDE.md). The acceptance bar is
parity, defined in §5.

---

## 1. Why this spec exists

Adding Liar's Dice widens the `GameModule` contract and splits the turn loop. PD is
the incumbent that runs through all of it. If we are not careful, "generalize the
platform" silently changes PD's scores, payloads, or timing. This spec pins down
exactly what changes on the PD path (almost nothing) and how we prove it.

The principle from the architecture docs: **PD is the default.** Every new contract
hook's default implementation reproduces today's PD behavior, so inheriting the
default and running PD must be indistinguishable from today.

---

## 2. Changes on the PD path

### 2.1 Extract `SimultaneousDriver` (the riskiest change)

- Move the body of `scheduler._run_game` (round reset → per-turn talk→act →
  resolve-all → `award_round` → `finalize`) into `SimultaneousDriver.run_match`.
- **Move, do not rewrite.** The talk/act helpers (`_open_turn`, `_wait_for_turn`,
  `_wait_for_messages`, `_begin_act_phase`) move to the shared
  `GameLoopContext`/helper module and are called unchanged.
- The scheduler skeleton selects `SimultaneousDriver` because PD's
  `config_defaults().simultaneous` is `True`.
- Resume-on-restart for PD is unchanged (reads `current_round`/`current_turn`).

### 2.2 Implement the new contract hooks as PD defaults

On `HoardHurtHelp` (or as protocol defaults PD inherits):

| Hook | PD implementation |
|---|---|
| `is_match_over` | `match.rounds_awarded >= match.total_rounds` |
| `final_placement` | players ordered by `(total_round_wins desc, total_round_score desc)` — the existing tiebreaker |
| `default_move` | `{"action": "HOARD"}` |
| `private_state_for` | `{}` |
| `public_state_for` | wraps PD's existing payload builder (see 2.3) |
| `on_round_start` | no-op (the score reset stays where it is today) |
| `next_actor` | not implemented (simultaneous games never call it) |

None of these change behavior; they relocate decisions the scheduler/records made
inline so the platform can ask the module instead of assuming PD.

### 2.3 Payload behind the contract

- Today `app/routes/agent_api.py` builds PD's `TurnSummary` / history directly via
  `turn_summary.py`, `board_signals.py`, `opponent_stats.py`.
- Route this through `module.public_state_for` (+ `private_state_for`). PD's
  implementation **calls the same builders**, so the bytes are identical; the agent
  API stops hard-coding PD shapes.
- Transitional option to lower risk: keep the existing PD payload path as the
  literal body of PD's `public_state_for`, so the diff is a move, not a rewrite.

### 2.4 Wire format passthrough

- `SubmitRequest` gains optional `move: dict` (LD). PD ignores it and keeps reading
  `action`/`target_id`. The submit route builds the generic `move` dict from PD's
  fields exactly as today when `move` is absent.

### 2.5 Storage

- The new `match_state` / `player_state` tables and the `quantity`/`face` columns
  on `turn_submissions` are additive. PD writes none of them. No PD migration data
  work; the migration is shared with the LD spec.

---

## 3. What explicitly does NOT change

- PD scoring (`resolver.py`), rules text (`rules.py`), and constants — untouched.
- PD's talk→act two-phase structure and deadlines — untouched.
- PD's storage columns and round/match scoring — untouched.
- PD's Sims (`app/engine/sims/strategies.py`) and the Practice Arena/auto-match
  seeding — untouched.

### Deferred (tracked, not done here)

- **Relocating PD's engine** (`resolver.py`, `rules.py`, `turn_summary.py`,
  `board_signals.py`, `opponent_stats.py`, `game_insights.py`) out of the shared
  `app/engine/` namespace into `app/games/hoard_hurt_help/`. This is the "pure"
  finish of the platform/game split. It is a large, behavior-neutral move that the
  game framework (feature 004) intentionally deferred, and we keep deferring it: the
  contract + driver split already decouples behavior and blast radius. Do it as its
  own refactor PR later, never bundled with a game change.

---

## 4. Files touched

| File | Change |
|---|---|
| `app/engine/scheduler.py` | Extract skeleton; add driver selection. |
| `app/engine/turn_drivers.py` (new) | `TurnDriver`, `GameLoopContext`, `SimultaneousDriver` (moved PD loop). |
| `app/games/base.py` | New hooks with PD-reproducing defaults. |
| `app/games/hoard_hurt_help/game.py` | Implement the hooks (mostly defaults / thin wrappers). |
| `app/routes/agent_api.py`, `agent_next_turn.py` | Call `public_state_for` / `private_state_for`; unchanged output for PD. |
| `app/schemas/agent.py` | `move` on `SubmitRequest`; `your_private_state` / `public_state` (null for PD). |
| `app/engine/game_records.py` + Elo reader | Read `final_placement` instead of assuming round-wins. |

---

## 5. Acceptance: parity (the merge gate)

PD behavior must be byte-identical. Proven by:

- **SC-P1 — Test suite green, unmodified.** The full PD test suite and
  `tests/test_stub_game.py` pass without edits.
- **SC-P2 — Scoring parity.** For a fixed set of actions, per-player score deltas,
  round winners, and final standings match the pre-refactor resolver output
  (golden test).
- **SC-P3 — Payload parity.** For a recorded turn, the agent `get_turn` payload
  (now via `public_state_for`) is identical to the pre-refactor payload (golden
  JSON compare).
- **SC-P4 — Loop parity.** A seeded full PD match driven by `SimultaneousDriver`
  produces the same turn/round/finalize sequence and the same `winner_player_id`
  as today.
- **SC-P5 — Placement parity.** `final_placement` returns the same order PD's
  existing leaderboard/Elo derivation produced.

A useful technique: capture a recorded PD match (actions + payloads) **before** the
refactor and replay it as a golden fixture for SC-P2/SC-P3/SC-P4.

---

## 6. Risk & sequencing

- **Highest risk:** the `SimultaneousDriver` extraction (§2.1) — it moves the live
  turn loop. Mitigation: pure move (no logic edits) + SC-P4 loop parity + the
  existing resume/restart tests.
- **Sequencing:** land these PD-side changes (driver split, hooks-as-defaults,
  payload-behind-contract, wire passthrough, storage migration) **behind the
  unchanged PD** first, with parity green, *before* the Liar's Dice module is
  registered. That ordering matches the LD design's phasing (§11): platform seams
  1–4 ship and prove PD parity, then game #2 plugs in.
