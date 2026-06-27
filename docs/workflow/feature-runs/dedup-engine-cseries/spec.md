# Spec — Engine C-series duplication cleanup

## Summary

Eliminate the **C-series** accidental code duplication in `app/engine/` found by the
codebase duplication inventory. This is a **behavior-preserving refactor**: every
addressed cluster collapses its copied logic to one shared definition, and all prior
call sites delegate to it. No gameplay, scheduling, timing, or API behavior changes.

This run is **Claude-only**: Codex and Gemini CLIs are not installed in this
environment, so Claude and independent Claude sub-agents perform authoring,
implementation, and the adversarial review gates.

## Background

A prior duplication inventory grouped engine duplication into clusters C1–C8. The
top-3 cross-cutting clusters from a different ranking (B1/E1/D1) already shipped in
PR #551. This run takes the remaining **engine** clusters. The recurring shape is
"a canonical helper already exists (or should), but call sites re-inline the same
logic" — most dangerous across the two turn drivers (`turn_drivers.py` = sequential,
`scheduler_turn_loop.py` = simultaneous), which were deliberately isolated but copied
shared primitives between themselves.

This spec was revised after two adversarial reviews (feasibility + requirements);
the reconciliation is recorded at the end.

## Dispositions

Each cluster ends in exactly one disposition. "Deferred" and "not-a-true-duplicate"
are distinct outcomes — the second is a *correct* result, not incomplete work.

- **unified** — duplicated logic now lives in one shared definition; all prior call
  sites delegate to it.
- **not-a-true-duplicate** — on close reading the implementations differ in behavior
  and must NOT be merged; left as-is, with the divergence documented at both sites.
- **deferred** — a real duplicate that could not be safely unified this run. Allowed
  ONLY for the medium/high-risk clusters (C2, C4, C5, C8). The mechanical clusters
  **C1, C3, C6, C7 are non-deferrable** (must reach unified). A deferral must record
  the specific behavior risk and the concrete characterization test that demonstrates
  it, and must be approved at a review gate — not declared unilaterally in closeout.

## In scope — the clusters

| ID | Cluster | Anchor locations (verified) | Shared target + home | Risk |
|----|---------|------------------------------|----------------------|------|
| C1 | Poll constant + UTC-now split across drivers | `turn_drivers.py:33,50`, `scheduler_turn_loop.py:47` (+ ~5 inline `datetime.now(timezone.utc)` on the simultaneous side) | one `_SUBMIT_POLL_SECONDS` (both already `0.25`) and one tz-aware UTC-now helper, reused by both drivers | low |
| C2 | Turn-row creation (`_open_turn` vs `_open_actor_turn`) | `scheduler_turn_loop.py:281` (phase="talk", resume get-or-create, sets `current_round`+`current_turn`) vs `turn_drivers.py:192` (phase="act", blind INSERT, sets `current_turn` ONLY) | **THREE** parametrized axes: `phase`, `resume_guard` (get-or-create on/off), `set_current_round` (on/off). If a single clean opener can't preserve all three without contortion → disposition **not-a-true-duplicate**. | **high** |
| C3 | "is this a bot?" predicate ×3 | `turn_drivers.py:152` (DB), `user_match_start.py:44` (value), `arena.py:297` (inline inverse) | one value-level `is_bot_kind(kind)`; DB variant calls it. (Verified safe: `AgentKind(str, Enum)` so `== BOT` and `in (BOT, BOT.value)` are equivalent.) Home: `user_match_start.py` (existing) or a small domain module. | low |
| C4 | "active / non-left player count" re-inlined | standalone counts only: `scheduler.py:95` (confirmed: left+reserved), `scheduler.py:313` (watchdog: left only), `arena.py:98` (confirmed), `arena.py:109/113` (seated: left only) | a count helper **parameterized by whether reserved seats are excluded**, preserving the confirmed-vs-seated distinction. Home: alongside `_active_player_count` in `scheduler.py`, or a `player_counts.py` if a cycle-free shared home is needed. | medium |
| C5 | `_has_moved` ×2 + `_PREGAME_STATES`/`_UPCOMING_STATES` constant | `connection_activity.py:80`, `agent_onboarding.py:127` (byte-identical queries — verified); constant at `connection_activity.py:44`, `agent_onboarding.py:34`, `agent_idle.py:82` | one `_has_moved` and one named pregame-states constant (pick one name + cycle-free home). Keep the two onboarding **enums/state machines distinct** — share only these primitives. | medium |
| C6 | Liveness-window check re-inlined | `connection_health_badge.py:34` (canonical `_within_window`, uses `ensure_aware`); window *expression* re-inlined inside `_connection_is_live` (`connection_health_badge.py:~322-325`) and reached via it from `provider_readiness.py:186` (through `_connection_is_live` at `provider_readiness.py:183`) | route only the trailing **window expression** through `_within_window`. **Do NOT replace `_connection_is_live` wholesale** — it has a `ConnectionStatus.PAUSED` early-return (`~319-320`) and None guards that `_within_window` does not; those stay. `_within_window` is `_`-private; relocate/rename to a shared domain home if imported cross-module. | low |
| C7 | Redundant standings sort re-inlined | `agent_play_reads.py:183` `_public_standings` inlines `(-current_round_score, seat_name)`, the exact key of `_scoreboard_order` (`agent_play_reads.py:140`) | `_public_standings` calls `_scoreboard_order`. | low |
| C8 | Match-cancel transition inlined ~6× | inline at `scheduler.py:185,320,401`, `scheduler_turn_loop.py:215`, `arena.py:189,301,332` — each sets `state=CANCELLED; cancelled_at=<now>`. **The sites are NOT uniform** (review correction): `scheduler.py:185/320/401` + `arena.py:301/332` use a *captured batch* `now`; `arena.py:189` + `scheduler_turn_loop.py:215` use a *fresh inline* `datetime.now(timezone.utc)`; **all** sites already do their own `await db.commit()` right after; none call `registry.stop`. | a tiny field-only `_mark_cancelled(match, now)` taking `now` as a **parameter**. Each caller passes its EXISTING `now` (captured or fresh — unchanged per site) and keeps its own commit + logging. NOT `cancel_match` (that one adds `registry.stop` + a fresh `now` + commit; it may be refactored to call the helper with its own fresh `now`). **Home must be cycle-free**: `match_deletion.py` imports `scheduler.registry`, so the helper cannot live where `scheduler.py`/`arena.py` re-import `scheduler` — prefer `state_machine.py`. | medium |

### Wave priority
- **High-risk (most review + characterization tests first):** C2.
- **Medium:** C4 (count-filter semantics), C5 (constant fan-out), C8 (cancel side effects + import cycle).
- **Low / mechanical (non-deferrable):** C1, C3, C6, C7.

## Required characterization tests (written and committed BEFORE the matching refactor)

These exist so the "full pytest is the oracle" claim is real — each must FAIL if the
pre-refactor behavior is altered. Current coverage gaps were confirmed by review.

1. **C2-seq:** `_open_actor_turn` (sequential) does NOT modify `game.current_round`,
   sets only `current_turn`, and blind-inserts (no get-or-create resume reuse).
2. **C2-sim:** `_open_turn` (simultaneous) sets BOTH `current_round` and
   `current_turn`, and calling it twice for the same `(round, turn)` returns the same
   row (get-or-create resume contract).
3. **C4-watchdog:** an ACTIVE game whose only seats are HELD (`seat_reserved_until`
   set, `left_at IS NULL`) is NOT cancelled by `_watchdog` (it counts `left_at`-only),
   while `_active_player_count` (start floor) EXCLUDES the held seat. Pins both filters.
4. **C5-precedence:** `_has_moved` returns True for a non-defaulted submission and
   False for a defaulted one; onboarding-state precedence ordering is unchanged for
   both `OnboardingState` and `AgentOnboardingState`.
5. **C8-cancel:** a cancelled match gets `state=CANCELLED` and `cancelled_at` equal
   to the `now` passed to `_mark_cancelled`, and gains NO new side effect — no inline
   site acquires a `registry.stop` call, and each site keeps its own commit. Pins
   that the helper is field-only and that the per-site fresh-vs-captured `now` choice
   is preserved.

## Out of scope (non-goals)

- Any functional/behavioral change to gameplay, scheduling timing, deadlines, or APIs.
- The non-engine inventory items (A1 datetime sweep, B3/B4 route helpers, F2/F3) —
  deferred to a separate effort.
- Cluster **C7's documented-intentional** dict-vs-schema scoreboard pair
  (`build_public_scoreboard_dicts` vs `_public_scoreboard`); only the redundant
  re-inlined *sort* in `_public_standings` is in scope.
- C4's `_all_submitted`/`_all_messaged` (`scheduler_turn_loop.py:330,347`): the
  seated-filter is embedded in a larger count-and-compare; not extractable as a count
  call. Left as-is.
- C4's `used_names` seat-name list query (`arena.py:119`): a name list, not a count.
- C5's inline `.in_([SCHEDULED, REGISTERING])` lists (`scheduler.py:168,386`,
  `user_match_start.py:74,119`, `agent_play.py:116`, `arena.py:159,237,276`) — only
  the three named *constants* are unified this run. `agent_play_next_turn.py:340`
  additionally includes `ACTIVE` (a DIFFERENT set) and must NOT be folded in.
- Health-`build()` closure unification beyond what falls out cleanly.

## Constraints

- Behavior-preserving only. Where two implementations legitimately diverge, prefer the
  **not-a-true-duplicate** disposition over a risky merge; document the divergence at
  both sites.
- CLAUDE.md Python standards: full type annotations, no `# type: ignore` / `# noqa`,
  no bare `except`, fail-loud, async consistency, no vague filenames
  (`utils.py`/`helpers.py`). Any NEW shared module gets a domain noun (e.g.
  `player_counts.py`, `onboarding_states.py`); any symbol promoted to cross-module
  use drops its leading-underscore "private" prefix. New homes are named in `plan.md`.
- Avoid import cycles: C8 (and any relocated C4/C5/C6 symbol) must land in a module
  that does not create a cycle with `scheduler.py`. The plan must state each new
  symbol's home and assert no cycle.
- This is NOT a small change (many `app/engine/` files, engine logic) — the full
  Preflight Gate and normal delivery path apply, not the small-change lane.
- One feature per branch (`claude/dedup-engine-cseries`). Commit per cluster (or per
  small group) so a single risky cluster can be reverted without losing the others.

## Acceptance criteria

1. **Per-cluster disposition recorded.** Every C1–C8 cluster ends as `unified`,
   `not-a-true-duplicate`, or (only C2/C4/C5/C8) `deferred` with the required
   risk+test justification. C1, C3, C6, C7 MUST be `unified`.
2. **Removal proven by presence, not just absence.** For each `unified` cluster, each
   old call site imports/calls the shared symbol, AND a stated check passes — e.g.
   `rg 'def _has_moved' app/engine/` returns exactly one definition;
   `rg 'def _SUBMIT_POLL_SECONDS|_SUBMIT_POLL_SECONDS =' app/engine/` shows one
   assignment. The exact check per cluster is listed in `tasks.md`.
3. **No behavior change**, specifically pinned: identical turn sequencing and the C2
   `current_round`/resume contract per mode; confirmed-vs-seated counts kept distinct
   (C4); identical bot detection (C3); identical `_has_moved` truth and onboarding
   precedence (C5); identical liveness windows AND the preserved `PAUSED` early-return
   (C6); identical standings order (C7); identical cancel side effects — each site's
   `now` (fresh OR captured, unchanged per site), no new `registry.stop`, and each
   site's own commit/logging kept (C8).
4. **Characterization tests land before their refactor** (the five above: C2-seq,
   C2-sim, C4-watchdog, C5-precedence, C8-cancel), each shown to fail under a
   deliberately wrong merge, plus any other test needed where engine logic is
   touched. No existing test is removed, `skip`ped, or `xfail`ed. The baseline test
   count is **measured on the branch base at run start** (`pytest -q` collected count
   recorded in `tasks.md`), and the final count must be ≥ that measured baseline +
   the new characterization tests — not a hardcoded literal.
5. **Full Preflight Gate green:** `ruff check . && mypy app/ mcp_server/ && pytest -q`.
6. **PR includes a `Validation` section** (CLAUDE.md) listing the exact `ruff`, `mypy`,
   and full `pytest` results (not the fast lane) and the final test count.

## Risks

- **C2 behavior drift (highest).** Mitigation: C2-seq/C2-sim characterization tests
  first; if a clean 3-axis opener is contorted, choose `not-a-true-duplicate`.
- **C4 count-semantics collapse.** `confirmed` (left+reserved) vs `seated`/watchdog
  (left only) must stay distinct. Mitigation: C4-watchdog test pins both filters.
  *verification:* run the C4-watchdog test against the refactor; a held-seat-only
  ACTIVE game must remain un-cancelled and `_active_player_count` must exclude it.
- **C8 import cycle / side-effect drift.** Mitigation: field-only `now`-parameterized
  helper in a cycle-free home; do not absorb registry.stop/logging/commit.
  *verification:* `python -c "import app.engine.scheduler, app.engine.arena, app.engine.state_machine"` imports clean (no cycle); grep confirms no inline cancel site gained a `registry.stop` call.
- **C5 constant fan-out under-scoped.** Mitigation: scope limited to the three named
  constants; inline `.in_()` lists explicitly out of scope (and the ACTIVE-including
  one excluded). *verification:* the unified constant has one definition and the
  ACTIVE-including set at `agent_play_next_turn.py:340` is untouched.

## Verification of "no behavior change"

The full `pytest` suite is the regression oracle, BUT it is only valid for the
divergent paths once the five characterization tests above exist — they are the part
of the oracle that actually fails on a wrong merge. Each slice keeps the full gate
green; the C2/C4/C5/C8 slices additionally run their characterization tests.

## Adversarial review reconciliation (spec stage)

Two independent Claude sub-agent lenses reviewed the first draft.

| # | Finding (lens) | Severity | Resolution |
|---|----------------|----------|------------|
| 1 | C2 omits the `current_round`-write axis; "phase+resume" is incomplete (both) | blocker | Added 3rd axis `set_current_round`; allow `not-a-true-duplicate`; C2-seq/C2-sim tests required. |
| 2 | "pytest is the oracle" unvalidated for C2/C4 divergences (both) | blocker | Added required characterization tests written BEFORE refactor; criterion 4. |
| 3 | C8 "unify to `cancel_match`" is wrong target (both) | major | Re-targeted to field-only `_mark_cancelled(match, now)`; `cancel_match` not the shared target. |
| 4 | C8 import cycle (`match_deletion`→`scheduler.registry`) (feasibility) | major | Constraint added: cycle-free home (prefer `state_machine.py`); import-clean verification. |
| 5 | C4 `_all_*` not extractable as a count; anchor list over-claims (feasibility) | major | Narrowed C4 anchors; `_all_*` moved to out-of-scope. |
| 6 | C4 watchdog filter untested (feasibility) | major | C4-watchdog characterization test required. |
| 7 | Deferral escape hatch is a loophole (requirements) | major | Dispositions section: mechanical floor non-deferrable; deferral needs risk+test+gate approval. |
| 8 | "grep gone" not verifiable (requirements) | major | Criterion 2 rewritten to presence-of-import + exact per-cluster checks in tasks. |
| 9 | "not-a-true-duplicate" disposition missing (requirements) | major | Added as a first-class disposition. |
| 10 | Shared-symbol homes/naming too vague (both) | major | Constraint: name homes in plan; domain nouns; drop `_` on cross-module symbols. |
| 11 | CLAUDE.md `Validation` section + per-cluster commits not required (requirements) | major | Criterion 6 + per-cluster commit constraint. |
| 12 | C5 `_PREGAME_STATES` fan-out wider than 3; one set includes ACTIVE (feasibility) | minor | Out-of-scope list enumerates inline `.in_()` sites; ACTIVE set excluded. |
| 13 | C1 timing assertion (requirements); C3/C6/C7/C5-primitives confirmed safe (both) | minor | C1 tz-aware/byte-identical-deadline note; safe clusters recorded. |

Both verdicts: **feasible / sufficient with these changes**, now incorporated.

### Round 2 (official Claude-only review path, `prepare-claude-reviews`)

After rebasing onto the updated engine, the spec was re-reviewed via the supported
Claude-only path. New corrections incorporated:

| # | Finding (lens) | Severity | Resolution |
|---|----------------|----------|------------|
| 14 | C8 sites are NOT uniform — some use a *fresh* `datetime.now()`, and ALL have their own per-site commit (both) | major | C8 row + criterion 3 corrected: per-site `now` (fresh/captured) and per-site commit preserved; helper is field-only. |
| 15 | C8 has no characterization test despite subtle side-effects (requirements) | major | Added C8-cancel characterization test (test #5). |
| 16 | C6 must keep the `PAUSED` early-return — only the window *expression* delegates, not all of `_connection_is_live` (feasibility) | major | C6 row + criterion 3 corrected: do not replace `_connection_is_live`; keep PAUSED/None guards. |
| 17 | Test-count baseline "1291" is brittle/unverifiable (both) | major | Criterion 4 now measures the baseline on the branch base at run start; no hardcoded literal. |
| 18 | Several anchor line numbers drift by ~1 (both) | minor | tasks.md resolves anchors by symbol/grep, not by line (already required by criterion 2). |

