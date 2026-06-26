# Plan — Engine C-series duplication cleanup

Behavior-preserving dedup of engine clusters C1–C8. Built on the checkpointed
`spec.md` and `reuse-report.md`. Every shared symbol's home is named here (spec
constraint). All homes are leaf-ish modules importing only `app.models.*` (or the
existing owner), so no import cycle with `scheduler.py`/`arena.py` is created.

## Module homes (final decisions)

| Cluster | New/owner module | Symbol(s) | Importers | Cycle note |
|---|---|---|---|---|
| C1 | **new** `app/engine/turn_clock.py` | `SUBMIT_POLL_SECONDS = 0.25`, `now_utc() -> datetime` | both drivers (`turn_drivers.py`, `scheduler_turn_loop.py`) | leaf (imports `datetime` only). Avoids driver→driver import. `_now()`/inline `datetime.now(timezone.utc)` replaced by `now_utc()`. |
| C2 | keep both openers; extract shared body to **new** `app/engine/turn_opener.py` ONLY if clean | `open_turn_row(db, game, round_num, turn_num, *, phase, resume_guard, set_current_round) -> Turn` | both drivers | leaf (imports models + `turn_clock`, `tokens`). Uses `turn_clock.now_utc()` (standardizes the C2 now-source — feasibility minor #2). |
| C3 | owner `app/engine/user_match_start.py` | promote `_is_bot` → `is_bot_kind(kind: object) -> bool` | `turn_drivers._is_bot` (DB) calls it; `arena.py:297` inline calls it | `user_match_start` is already imported widely; no new cycle. |
| C4 | **new** `app/engine/player_counts.py` | `active_player_count(db, match_id, *, exclude_reserved: bool) -> int` | `scheduler.py` (×2), `arena.py` (×2) | leaf (imports `Player` model + sqlalchemy). Avoids the latent `arena`→`scheduler` cycle (arena already defers its scheduler import — feasibility risk). |
| C5 | **new** `app/engine/onboarding_states.py` | `PREGAME_STATES = (SCHEDULED, REGISTERING)`, `has_moved(db, agent_id: int) -> bool` | `connection_activity.py`, `agent_onboarding.py`, `agent_idle.py` (constant) | leaf (imports `GameState` + models). Breaks the two-onboarding-module mutual-import risk. State enums/machines stay in their modules. |
| C6 | owner `app/engine/connection_health_badge.py` | promote `_within_window` → `within_window` | `provider_readiness.py` (already imports nothing from health_badge — verify) | only the trailing window *expression* in `_connection_is_live` and `provider_loop_running` delegates; **PAUSED early-return + None guards stay**. |
| C7 | owner `app/engine/agent_play_reads.py` | reuse `_scoreboard_order` | `_public_standings` (same file) | trivial, same-file. |
| C8 | **new** `app/engine/match_cancellation.py` | `mark_cancelled(match, now: datetime) -> None` (field-only: sets `state=CANCELLED`, `cancelled_at=now`) | `match_deletion.cancel_match` + 6 inline sites in `scheduler.py`/`arena.py`/`scheduler_turn_loop.py` | leaf (imports `Match`/`GameState`). **Not** `state_machine.py` (keeps that module's pure-transition contract — feasibility minor #1). Each caller passes its OWN `now` (fresh or captured, unchanged) and keeps its own `commit`/logging; `cancel_match` keeps `registry.stop`. |

Reuse-report rows all addressed: C1/C2/C4/C5/C8 use named (mostly new, tiny,
domain-named) homes; C3/C6/C7 reuse/promote existing owners. No `utils.py`/`helpers.py`.

## Waves & slices (each slice = one `[CHECKPOINT]`, ≤~300 lines, own commit)

Ordered so mechanical low-risk clusters land first and the risky clusters get
characterization tests committed *before* their refactor.

- **Slice 0 — baseline:** record `pytest -q` collected count on the branch base into
  `tasks.md` (criterion 4 measured baseline). No code change.
- **Slice 1 — C1 (turn_clock):** add `turn_clock.py`; replace the two
  `_SUBMIT_POLL_SECONDS` defs + `_now()` + inline `datetime.now(timezone.utc)` in the
  two drivers. Low risk.
- **Slice 2 — C3 (is_bot_kind):** promote predicate in `user_match_start.py`; rewire
  `turn_drivers._is_bot` + `arena.py:297`. Low risk.
- **Slice 3 — C6 (within_window):** promote `within_window`; delegate the window
  expression in `_connection_is_live` and `provider_loop_running`, keeping PAUSED/None
  guards. Low risk.
- **Slice 4 — C7 (standings sort):** `_public_standings` calls `_scoreboard_order`.
  Low risk.
- **Slice 5 — C4 tests then refactor:** commit C4-watchdog characterization test
  FIRST (held-seat ACTIVE not cancelled by `_watchdog`; `active_player_count` excludes
  held); then add `player_counts.py` and rewire the 4 standalone count sites with the
  correct `exclude_reserved` per site. Medium.
- **Slice 6 — C5 tests then refactor:** commit C5-precedence test FIRST; then add
  `onboarding_states.py` and rewire `_has_moved` (×2) + the 3 named constants. Medium.
- **Slice 7 — C8 tests then refactor:** commit C8-cancel test FIRST (field-only helper,
  no new `registry.stop`, per-site commit/now preserved, all 6 sites delegate); then add
  `match_cancellation.py`, rewire `cancel_match` + the 6 inline sites. Medium.
- **Slice 8 — C2 tests then refactor (highest risk, last):** commit C2-seq + C2-sim
  tests FIRST; then attempt the 3-axis `open_turn_row`. **Disposition gate:** if the
  unified opener is contorted, land C2 as `not-a-true-duplicate` (document divergence
  at both sites) instead of forcing the merge. Either outcome keeps the C2 tests.

Each slice runs the full Preflight Gate; the C4/C5/C8/C2 slices additionally run their
characterization tests. Diff-checkpoint (Claude regression-adversarial) on every slice
≥50 changed lines.

## Disposition tracking

A running table in `tasks.md` records each cluster's final disposition (`unified` /
`not-a-true-duplicate` / `deferred`) + its presence-check command. C1/C3/C6/C7 must be
`unified`. For C8 the presence check asserts **all 6 inline sites** call
`mark_cancelled` (not just "one def") — closes the requirements multi-site minor.

## Residual Risks

- **C2 unify-vs-defer is a subjective call.** The 3-axis opener may read as contorted.
  *verification:* C2-seq + C2-sim tests must pass under whichever disposition is chosen;
  if unified, a reviewer confirms the opener has exactly the 3 axes and no 4th hidden
  behavior (now-source standardized via `turn_clock`). If the opener needs a 4th
  special-case branch, default to `not-a-true-duplicate`.
- **C4 wrong filter mode at a non-watchdog site.** The watchdog test pins 2 of 4 sites.
  *verification:* add an assertion/test that `arena.py` confirmed sites use
  `exclude_reserved=True` and the seated site uses `exclude_reserved=False`; grep each
  rewired call site for its explicit keyword before merge.
- **C8 incomplete site conversion.** A refactor could leave an inline site behind.
  *verification:* `rg "state\s*=\s*GameState.CANCELLED" app/engine/` returns only
  `match_cancellation.py` (the helper) after the slice; every prior site shows a
  `mark_cancelled(` call. C8-cancel test asserts no new `registry.stop`.
- **New module introduces an import cycle.** *verification:*
  `python -c "import app.engine.scheduler, app.engine.arena, app.engine.turn_drivers, app.engine.scheduler_turn_loop, app.engine.turn_clock, app.engine.player_counts, app.engine.onboarding_states, app.engine.match_cancellation"`
  imports clean (exit 0) at the end of each slice that adds a module.
- **C6 over-merge drops the PAUSED short-circuit.** *verification:* the connection-health
  tests still pass AND a grep confirms `_connection_is_live` retains its
  `ConnectionStatus.PAUSED` early-return after the change.
- **Baseline non-determinism (rebase adds/removes tests).** *verification:* re-measure
  the `pytest -q` collected count on the final branch head; final ≥ recorded baseline +
  new characterization tests, with no test `skip`/`xfail`ed (grep the diff).

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
