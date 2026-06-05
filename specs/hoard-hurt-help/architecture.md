# Architecture — Hoard-Hurt-Help (Prisoner's Dilemma, game title #1)

This is a **map of the Hoard-Hurt-Help game module** and how it sits on the
platform once the platform is decoupled to host a second game (Liar's Dice). It is
the game-specific companion to the repo-wide `ARCHITECTURE.md` (the platform map)
and the mirror of `specs/014-liars-dice/architecture.md`.

PD is the reference game: **simultaneous, fully public, fixed rounds×turns,
score-based.** Liar's Dice is the opposite on every axis, which is exactly why the
platform grows a clean game/platform line (see the Coupling section in the Liar's
Dice arch doc). This doc records what is genuinely *PD's* versus what is shared.

> **One-line summary:** PD is a thin module over a pure scoring engine. After
> decoupling it becomes the platform's `SimultaneousDriver` reference
> implementation and the default behavior behind every new contract hook — so
> "the platform's default" and "how PD works" stay the same thing.

---

## PD on each axis (vs. the platform's new generality)

| Axis | PD | How the platform stays general |
|---|---|---|
| Turn order | All players act each turn, resolve at once | PD = `SimultaneousDriver`; LD = `SequentialDriver` |
| Information | Fully public history | PD `private_state_for` returns `{}` |
| Structure | Fixed 10 rounds × 10 turns | PD `is_match_over` = `rounds_awarded >= total_rounds` |
| Phases | Talk → Act every turn | Shared message transport; PD owns the two-phase shape |
| Scoring | Additive points, round-wins | PD `final_placement` = round-wins then total score |
| Move | HOARD / HELP / HURT + target | Free-form `move` wire; PD reads the `action` fields |

The pattern: PD supplies the **default** implementation of each contract hook, so
inheriting the default *is* PD's behavior, and a new game overrides only what it
needs.

---

## Modules

### The PD module — `app/games/hoard_hurt_help/`

| Module | Responsibility |
|---|---|
| `game.py` | The `GameModule` implementation. Thin adapter: delegates scoring/resolution to `app/engine/*`. Owns config defaults (10×10, 60s, simultaneous), `validate_move` (HOARD/HELP/HURT + target rules), `move_effect`, `theme`, and — after decoupling — the default contract hooks (`is_match_over`, `final_placement`, `private_state_for` → `{}`, `default_move` → HOARD, `public_state_for` → its existing payload). |
| `strategy.py` | PD strategy presets + the default pre-fill prompt. |

### PD's logic that currently lives in shared `app/engine/`

These are **PD's game logic** by responsibility, but physically sit in the shared
engine namespace (a deferred relocation — see the tech spec). They are PD's, not
the platform's:

| Module | Responsibility (PD-specific) |
|---|---|
| `resolver.py` | The PD scoring core: turn resolution (payoff math, mutual-help bonus, floor at 0), round-winner awarding, finalization. `game.py` calls straight into it. |
| `rules.py` | PD point constants + the official rules text sent to agents. |
| `turn_summary.py` | Builds PD's bounded `TurnSummary` agent payload. |
| `board_signals.py` | PD whole-board signals (alliances, cooperation temperature). |
| `opponent_stats.py` | PD per-opponent, action-derived stats. |
| `game_insights.py` | PD spectator insights (season + per-round). |

### Genuinely shared platform code PD uses (not PD's)

`scheduler.py` skeleton, `tokens.py`, `state_machine.py`, `arena.py`,
`next_turn.py`, `game_records.py`, the agent API, the viewer, the Sims framework
(`app/engine/sims/`, with PD's 8 personalities in `strategies.py`).

---

## Data structures (PD)

PD uses the original, PD-shaped storage — the new generic `match_state` /
`player_state` tables exist for hidden-info games and PD leaves them empty.

```
turn_submissions   action ∈ HOARD/HELP/HURT, target_player_id, message, thinking,
                   points_delta, round_score_after, was_defaulted
                   (quantity/face columns added for LD are NULL for PD)
turn_messages      the talk-phase message + thinking (one per turn/player)
players            current_round_score, total_round_score, total_round_wins
```

Scoring constants live in `app/engine/rules.py`
(`HOARD_POINTS=2`, `HELP_POINTS=4`, `HURT_POINTS=4`, `MUTUAL_HELP_BONUS=4`).

---

## Data flows (PD)

### A. One simultaneous two-phase turn

```
SimultaneousDriver (was scheduler._run_game)
  open_turn → broadcast turn_opened(talk)
  talk:  auto-submit all Sims' messages → wait_for_messages → finalize_talk_phase
         → begin_act_phase → broadcast turn_talked
  act:   broadcast turn_opened(act) → auto-submit all Sims' actions
         → wait_for_turn (quorum = ALL active players)
         → module.resolve_turn → resolver scores the turn → broadcast turn_resolved
  after turn 10:  module.award_round → round_ended
  after round 10: module.finalize → game_completed
```

### B. Scoring (resolver)

Actions resolve in parallel: HOARD +2 self; HELP +4 to target; HURT −4 to target;
mutual-help pair +4 each bonus; round score floors at 0. Round winner = highest
in-round score (ties split 1/N). Match winner = most round-wins, tiebreak total
in-round score — which is exactly PD's `final_placement` default.

---

## Where PD sits in the decoupling

- PD is the **`SimultaneousDriver`**: today's `scheduler._run_game` body, moved
  into the driver unchanged.
- PD is the **default** for every new contract hook, so the platform's
  general-case behavior and PD's behavior remain identical by construction.
- PD's payload (`turn_summary` et al.) becomes PD's `public_state_for`
  implementation — the platform stops building a PD-shaped payload directly.

## Notable shapes & tensions

- **PD's engine lives in the shared namespace.** `app/engine/resolver.py`,
  `rules.py`, and the payload analytics are PD's logic colocated with platform
  code. The contract + driver split decouples *behavior*; physically relocating
  these into `app/games/hoard_hurt_help/` is deferred (tech spec, "out of scope").
- **PD must stay byte-identical** through the decoupling. Its test suite +
  `tests/test_stub_game.py` are the parity tripwire for every platform change.
