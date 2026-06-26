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

## In scope — the clusters

| ID | Cluster | Anchor locations | Unify to |
|----|---------|------------------|----------|
| C1 | Poll-loop constant + `_now()` split across the two drivers | `turn_drivers.py:33,50`, `scheduler_turn_loop.py:47` | one `_SUBMIT_POLL_SECONDS` + one UTC-now helper |
| C2 | Turn-row creation (`_open_turn` vs `_open_actor_turn`) | `scheduler_turn_loop.py:281`, `turn_drivers.py:192` | one parametrized turn-opener (phase + resume-guard as params) |
| C3 | "is this a bot?" predicate ×3 | `turn_drivers.py:152` (DB), `user_match_start.py:44` (value), `arena.py:297` (inline inverse) | one value-level `is_bot_kind(...)` predicate; DB variant calls it |
| C4 | "active / non-left player count" re-inlined | canonical `scheduler.py:95`; re-inlined `scheduler.py:313`, `arena.py:98`, `scheduler_turn_loop.py:330,347` | route all counts through one query helper (preserving the confirmed-vs-seated distinction) |
| C5 | Parallel onboarding state machines + `_has_moved` ×3 + `_PREGAME_STATES` ×3 | `connection_activity.py:44,80,194`, `agent_onboarding.py:34,127`, `agent_idle.py:82` | one `_has_moved`, one `_PREGAME_STATES`/`_UPCOMING_STATES` constant; keep the two state enums/machines distinct but shared on the genuinely identical primitives |
| C6 | Liveness-window check re-inlined | `connection_health_badge.py:34` (canonical `_within_window`), re-inlined `connection_health_badge.py:311`, `provider_readiness.py:186` | route inline window checks through `_within_window` (via `ensure_aware`) |
| C7 | Redundant standings sort re-inlined | `agent_play_reads.py` `_scoreboard_order` vs re-inlined sort in `_public_standings` | the re-inlined sort calls `_scoreboard_order` |
| C8 | Match-cancel transition block inlined ~6× | canonical `match_deletion.py:33` `cancel_match`; inlined in `scheduler.py`, `arena.py` | extract the state+timestamp transition so inline sites reuse it where their surrounding registry/logging allows |

### Wave priority
- **High-risk (most review):** C2 (turn lifecycle), C5 (onboarding state machines).
- **Medium:** C4 (count semantics — confirmed vs seated must stay distinct), C8 (cancel side effects + registry teardown differ per site).
- **Low / mechanical:** C1, C3, C6, C7.

## Out of scope (non-goals)

- Any functional/behavioral change to gameplay, scheduling timing, deadlines, or APIs.
- The non-engine inventory items (A1 datetime sweep, B3/B4 route helpers, F2/F3) —
  deferred to a separate effort.
- Cluster **C7's documented-intentional** dict-vs-schema scoreboard pair
  (`build_public_scoreboard_dicts` vs `_public_scoreboard`); only the genuinely
  redundant re-inlined *sort* in `_public_standings` is in scope.
- Health-`build()` closure unification beyond what falls out cleanly.

## Constraints

- Behavior-preserving only. Where the two drivers legitimately diverge
  (sequential vs simultaneous), unify only the genuinely identical primitives and
  preserve deliberate differences — document each at the call site.
- CLAUDE.md Python standards: full type annotations, no `# type: ignore` / `# noqa`,
  no bare `except`, fail-loud (no swallowed errors), async consistency, no vague
  filenames (`utils.py`/`helpers.py`). New shared homes must be domain-named.
- One feature per branch (`claude/dedup-engine-cseries`); full Preflight Gate before push.

## Acceptance criteria

1. Each in-scope cluster's duplicated logic exists in exactly one place; all prior
   call sites delegate to it (verified by grep showing the inlined copies gone).
2. **No behavior change**: identical turn sequencing, deadlines, submission/message
   counting, bot detection, onboarding-state transitions, liveness windows,
   standings ordering, and match-cancel side effects (incl. registry teardown).
3. Full Preflight Gate green: `ruff check . && mypy app/ mcp_server/ && pytest -q`
   (≥1291 tests, the count at branch base).
4. New/updated tests lock in the unified helpers where engine logic is touched,
   especially C2 (turn-opener phase/resume parametrization), C4 (confirmed vs
   seated counts), and C5 (`_has_moved` + onboarding-state precedence).
5. Where a cluster cannot be safely unified without behavior risk, it is explicitly
   deferred in the closeout with the reason — partial completion is acceptable, a
   silent behavior change is not.

## Risks

- **C2/C5 behavior drift** — the highest risk. Mitigation: characterization tests
  before refactor where coverage is thin; diff-checkpoint review on these slices.
- **C4 count-semantics collapse** — `confirmed` (left+reserved) vs `seated` (left
  only) must not be merged into one filter. Mitigation: explicit test for both.
- **C8 cancel teardown** — inline sites also stop registry tasks / log differently;
  extracting only the state transition (not the surrounding teardown) avoids changing
  side effects. Mitigation: keep each site's registry/logging untouched.

## Verification of "no behavior change"

The Preflight Gate's full `pytest` suite (engine, scheduler, onboarding, connection
health, and match-lifecycle tests) is the primary behavior oracle; each slice must
keep it green. Slices touching C2/C4/C5 add targeted tests that pin the exact
pre-refactor behavior.
