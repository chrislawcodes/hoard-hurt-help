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
| C4 | **new** `app/engine/player_counts.py` | `active_player_count(db, match_id, *, exclude_reserved: bool) -> int` — `True` = confirmed (left+reserved filter), `False` = left-only | `scheduler.py` (`_active_player_count` body → `exclude_reserved=True`; `_watchdog` → **`exclude_reserved=False`**, it includes reserved seats), `arena.py` `fill_match_with_bots` calls it **twice** (confirmed `True` + seated `False` in one function) | leaf (imports `Player` model + sqlalchemy). Avoids the latent `arena`→`scheduler` cycle (arena already defers its scheduler import). |
| C5 | **new** `app/engine/onboarding_states.py` | `PREGAME_STATES = (SCHEDULED, REGISTERING)`, `has_moved(db, agent_id: int) -> bool` | `connection_activity.py`, `agent_onboarding.py`, `agent_idle.py` (which renames its `_UPCOMING_STATES`) | leaf (imports `GameState` + models). Breaks the two-onboarding-module mutual-import risk. State enums/machines stay in their modules. **Shim note:** `connection_activity.py` imports liveness via the `connection_health.py` re-export aggregator — if `has_moved` must stay importable through it, update `connection_health.py.__all__`. |
| C6 | owner `app/engine/connection_health_badge.py` | promote `_within_window` → `within_window` | `provider_readiness.py` (already imports `_connection_is_live` from here) | `_connection_is_live`: delegate only its trailing window *expression*; **keep PAUSED early-return + None guards**. `provider_loop_running` does NOT call `_within_window` today — it inlines a per-row loop with a DIFFERENT constant (`LOOP_RUNNING_WINDOW_SECONDS`); if rewired, pass that constant and keep the per-row None-skip loop (NOT a trivial delegate — verify the constant). |
| C7 | owner `app/engine/agent_play_reads.py` | reuse `_scoreboard_order` | `_public_standings` (same file) | trivial, same-file. |
| C8 | **new** `app/engine/match_cancellation.py` | `mark_cancelled(match, now: datetime) -> None` (field-only: sets `state=CANCELLED`, `cancelled_at=now`) | `match_deletion.cancel_match` + **7** inline sites: `scheduler.py:184,319,400`, `arena.py:188,300,331`, `scheduler_turn_loop.py:214` (review correction — was "6") | leaf (imports `Match`/`GameState`). **Not** `state_machine.py` (keeps that module's pure-transition contract). Each caller passes its OWN `now` (fresh or captured, unchanged) and keeps its own `commit`/logging; `cancel_match` keeps `registry.stop`. |

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
  no new `registry.stop`, per-site commit/now preserved, **all 7 sites** delegate); then
  add `match_cancellation.py`, rewire `cancel_match` + the **7** inline sites. Medium.
- **Slice 8 — C2 tests then refactor (highest risk, last):** commit C2-seq + C2-sim
  tests FIRST. **Disposition: `not-a-true-duplicate` is the EXPECTED outcome** — the two
  openers differ structurally (get-or-create query-and-branch vs blind INSERT; both vs
  turn-only `current_round` write; different now-source), so a clean 3-axis merge is
  unlikely. Only unify if the merged opener reads cleanly with no hidden 4th branch;
  otherwise document the divergence at both sites. If C2 defers, the planned now-source
  standardization onto `turn_clock.now_utc()` is forgone (status quo, not a regression).
  Either outcome keeps the C2 tests.

Each slice runs the full Preflight Gate. The C4/C5/C8/C2 characterization tests are
DB-backed (auto-tagged `integration`), so the test-bearing slices run the **full**
`pytest -q` (or target `pytest tests/test_<name>.py`), NOT the fast `-m "not integration"`
lane. Each characterization test is shown **red against a deliberately-wrong merge**
before the refactor makes it green. Diff-checkpoint (Claude regression-adversarial) on
every slice ≥50 changed lines.

## Disposition tracking

A running table in `tasks.md` records each cluster's final disposition (`unified` /
`not-a-true-duplicate` / `deferred`) + its presence-check command. C1/C3/C6/C7 must be
`unified`. For C8 the presence check asserts **all 7 inline sites** call
`mark_cancelled` (not just "one def") — closes the requirements multi-site minor.

## Residual Risks

- **C2 unify-vs-defer is a subjective call.** The 3-axis opener may read as contorted.
  *verification:* C2-seq + C2-sim tests must pass under whichever disposition is chosen;
  if unified, a reviewer confirms the opener has exactly the 3 axes and no 4th hidden
  behavior (now-source standardized via `turn_clock`). If the opener needs a 4th
  special-case branch, default to `not-a-true-duplicate`.
- **C4 wrong filter mode at a non-watchdog site.** Watchdog uses `exclude_reserved=False`
  (includes reserved); start-floor + arena-confirmed use `True`; arena-seated uses
  `False`. `fill_match_with_bots` calls the helper TWICE.
  *verification:* C4-watchdog test asserts a held-seat (reserved) ACTIVE game is NOT
  cancelled by `_watchdog` AND is excluded by the start-floor count; grep each rewired
  call site for its explicit keyword (expect two keywords in `fill_match_with_bots`).
- **C8 incomplete site conversion.** A refactor could leave one of the 7 inline sites
  behind. *verification:* an **assignment-scoped** check (not the broad CANCELLED grep,
  which also hits `state_machine.py` transition literals and `state in (…)` membership
  tests): `rg "\.state\s*=\s*GameState\.CANCELLED" app/engine/` returns only
  `match_cancellation.py` after the slice; each of the 7 prior sites shows a
  `mark_cancelled(` call. C8-cancel test asserts both fields take the passed `now` and
  no site gains a `registry.stop`.
- **New module introduces an import cycle.** *verification:*
  `python -c "import app.engine.scheduler, app.engine.arena, app.engine.turn_drivers, app.engine.scheduler_turn_loop, app.engine.turn_clock, app.engine.player_counts, app.engine.onboarding_states, app.engine.match_cancellation, app.engine.user_match_start, app.engine.provider_readiness"`
  imports clean (exit 0) at the end of each slice that adds a module. This must run
  AFTER the new import edges are added (e.g. `turn_drivers`→`user_match_start.is_bot_kind`
  for C3), since a cycle only appears once the import statement exists.
- **C6 over-merge drops the PAUSED short-circuit.** *verification:* the connection-health
  tests still pass AND a grep confirms `_connection_is_live` retains its
  `ConnectionStatus.PAUSED` early-return after the change.
- **Baseline non-determinism (rebase adds/removes tests).** *verification:* re-measure
  the `pytest -q` collected count on the final branch head; final ≥ recorded baseline +
  new characterization tests, with no test `skip`/`xfail`ed (grep the diff).

## Plan review reconciliation (round 1, Claude-only implementation + testability lenses)

| Finding (lens) | Sev | Resolution |
|---|---|---|
| C8 has **7** inline sites, not 6 (both) | major | Corrected count throughout (C8 row, slice 7, disposition tracking). |
| C8 presence-check regex too broad — matches transition literals + membership tests (impl) | major | Verification tightened to assignment-scoped `rg "\.state\s*=\s*GameState\.CANCELLED"`. |
| C2 get-or-create is a code path, not a flag → `not-a-true-duplicate` should be EXPECTED (both) | major | Slice 8 reframed: defer is the expected outcome; unify only if clean. |
| C4 helper must toggle reserved filter; watchdog value unnamed; `fill_match_with_bots` calls it twice (both) | major | C4 row + risk name `exclude_reserved` per site (watchdog `False`); note the two calls. |
| C6 `provider_loop_running` not a trivial delegate; different constant; `provider_readiness` already imports from health_badge (both) | minor | C6 row corrected: pass `LOOP_RUNNING_WINDOW_SECONDS`, keep per-row loop + PAUSED guard; verify constant. |
| C5 `connection_health.py` re-export shim + `agent_idle` rename + a `has_moved` equivalence test (both) | minor | C5 row notes the shim/rename; C5 test extended to pin `has_moved` boundary. |
| Fast lane skips DB-backed char tests; need red-then-green (testability) | minor | Slices run full `pytest -q`; each char test shown red-against-wrong-merge first. |
| C3 import-edge cycle only appears once the edge is added (impl) | minor | Import smoke test runs after edges added, includes `user_match_start`/`provider_readiness`. |

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
- review: reviews/plan.claude.implementation-adversarial.review.md | status: accepted | note: Round-2: all prior majors confirmed resolved; only minors remain (drop 'byte-identical' descriptor [done in reuse-report]; add C3 one-line is_bot_kind assertion, C6 naive-timestamp boundary test, C5 defaulted-only+mixed equivalence test). These test details are carried into tasks.md.
- review: reviews/plan.claude.testability-adversarial.review.md | status: accepted | note: Round-2: all prior majors confirmed resolved; only minors remain (drop 'byte-identical' descriptor [done in reuse-report]; add C3 one-line is_bot_kind assertion, C6 naive-timestamp boundary test, C5 defaulted-only+mixed equivalence test). These test details are carried into tasks.md.
- review: reviews/diff.claude.regression-adversarial.review.md | status: accepted | note: Whole-branch regression review: no behavior change found across all clusters (C4 exclude_reserved per site, C8 all 8 sites field-only with own now/commit + registry.stop kept, C6 PAUSED+LOOP constant preserved, C1/C5/C7 equivalent, C2 distinct). Two minors noted: Preflight lint/type run green on every slice; mark_cancelled commit-adjacency is advisory.
- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Spec findings incorporated (rounds 1-3): C2 not-a-clean-duplicate, oracle/characterization tests, C8 site characterization, C6 PAUSED guard, measured baseline. Final spec checkpoint clean.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Spec findings incorporated (rounds 1-3): C2 not-a-clean-duplicate, oracle/characterization tests, C8 site characterization, C6 PAUSED guard, measured baseline. Final spec checkpoint clean.
