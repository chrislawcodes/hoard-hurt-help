---
reviewer: codex
lens: feasibility-adversarial
stage: spec
model: claude-sub-agent
note: "Claude-only run: Codex/Gemini CLIs unavailable in this environment; this adversarial lens was produced by an independent Claude sub-agent acting as the feasibility reviewer."
resolution_status: "accepted"
resolution_note: "All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table."
---

# Feasibility-adversarial review — spec (engine C-series dedup)

Independent adversarial read of the first spec draft against every anchor file.
Findings ordered by severity.

## Blockers

- **F1 [blocker] C2 needs three params, not two.** `_open_turn`
  (`scheduler_turn_loop.py:300-301,316-317`) sets BOTH `game.current_round` and
  `game.current_turn`; `_open_actor_turn` (`turn_drivers.py:208`) sets ONLY
  `current_turn` (the sequential driver owns `current_round` in `run_match`,
  `turn_drivers.py:91-94`). A naive merge silently starts writing `current_round`
  on the sequential path (or breaks the simultaneous resume pointer). The spec's
  "phase + resume-guard" parametrization is incomplete.
- **F2 [blocker] No characterization test pins the sequential opener.** Only the
  simultaneous side has lifecycle tests (`test_scheduler_lifecycle.py:157,174`).
  `test_sequential_driver.py` never asserts `current_round`/`current_turn` after
  `_open_actor_turn`, nor the blind-insert (no get-or-create). A wrong unified
  opener would pass the whole suite. Tests required BEFORE refactor.

## Major

- **F3 [major] C8 cannot unify to `cancel_match`.** `cancel_match`
  (`match_deletion.py:40-43`) calls `registry.stop`, uses a FRESH timestamp, and
  commits immediately. Every inline site (`scheduler.py:184,319,400`,
  `scheduler_turn_loop.py:214`, `arena.py:188,300,331`) does none of those — they
  set `state`+`cancelled_at` from a CAPTURED batch-`now` and never stop the
  registry. Safe target is a field-only `_mark_cancelled(match, now)` taking `now`
  as a parameter.
- **F4 [major] C8 import cycle.** `match_deletion.py:10` does
  `from app.engine.scheduler import registry`, so `scheduler.py`/`arena.py` cannot
  import the transition from `match_deletion.py`. The helper needs a cycle-free
  home (e.g. `state_machine.py`). Spec was silent on this.
- **F5 [major] C4 anchor list over-claims.** `scheduler_turn_loop.py:330,347`
  (`_all_submitted`/`_all_messaged`) embed the seated-filter inside a
  count-and-compare; they are not extractable as a count call. Narrow C4 to the
  genuine standalone counts (`scheduler.py:95,313`, `arena.py:98,109/113`).
- **F6 [major] C4 watchdog filter untested.** `_watchdog` (`scheduler.py:313`)
  counts `left_at IS NULL` only. Nothing pins that it does NOT cancel a game whose
  only seats are HELD (`seat_reserved_until` set). Routing it through
  `_active_player_count` (reserved-aware) would change behavior with the suite
  staying green. Characterization test required.

## Minor

- **F7 [minor] C4 third filter.** `arena.py:119` selects a seat-NAME list
  (`used_names`), not a count; must not be collapsed into a count helper.
- **F8 [minor] C6 private cross-module import.** `_within_window` is `_`-private in
  `connection_health_badge.py`; importing into `provider_readiness.py` needs a
  shared home/rename. Behavior verified byte-identical to `ensure_aware` + None
  guard — safe.
- **F9 [minor] C5 constant fan-out wider than 3.** `(SCHEDULED, REGISTERING)` is
  inlined as `.in_([...])` at `scheduler.py:168,386`, `user_match_start.py:74,119`,
  `agent_play.py:116`, `arena.py:159,237,276`; `agent_play_next_turn.py:340` also
  includes `ACTIVE` (a DIFFERENT set — must NOT fold in). Name clash: `agent_idle`
  calls it `_UPCOMING_STATES`.

## Verified safe (non-issues)

- **C3** safe: `AgentKind(str, Enum)` (`agent.py:16`) makes `== BOT` and
  `in (BOT, BOT.value)` equivalent.
- **C5 `_has_moved`** (`agent_onboarding.py:127` vs `connection_activity.py:80`)
  byte-identical queries; the two state enums/machines have distinct precedence —
  sharing the primitives does not couple them.
- **C7** correctly scoped; **C1** both constants `0.25`, simultaneous side inlines
  `datetime.now(timezone.utc)`.

## Verdict

Feasible-with-changes. Low/mechanical clusters (C1, C3, C6, C7) and C5 primitives
are safe. C2 and C8 were mis-specified and C2/C4 lacked test coverage; the revised
spec must add the C2 third axis, re-target C8 to a `now`-parameterized field-only
helper with a cycle-free home, narrow C4, and add the named characterization tests
before refactor.

## Resolution
- status: accepted
- note: All blocker/major/minor findings incorporated into spec.md (revised clusters table, dispositions incl. not-a-true-duplicate, C2 3-axis params, C8 field-only _mark_cancelled with cycle-free home, narrowed C4, required characterization tests, tightened deferral floor, Validation deliverable). See spec reconciliation table.
