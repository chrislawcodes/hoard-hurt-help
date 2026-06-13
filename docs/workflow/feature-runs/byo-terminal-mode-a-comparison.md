# Experiment — BYO Terminal: Mode A (interactive MCP play) v1

**Date:** 2026-06-13 · **Repo:** hoard-hurt-help · **Type:** Backend / hot-path concurrency + schema

## Outputs

- **Direct Path:** branch `direct/byo-terminal-mode-a` (commit `6586d4f`) — full working implementation, preflight green (754 tests). **WINNER, merged.**
- **Feature Factory:** branch `feat/byo-terminal-mode-a` — spec + plan + reuse-report + 4 review rounds. **Implementation not built** — stopped after the plan stage once the Direct arm proved a correct, green implementation that already satisfied (and improved on) the plan; building near-identical FF code would have been redundant. FF record preserved under `feature-runs/byo-terminal-mode-a-factory/`.

## Did Reviews Change The Work?

| Stage | Path | Artifact | artifact_revised | issues_raised | issues_accepted | review_rounds |
|-------|------|----------|-----------------|---------------|-----------------|---------------|
| Implement | Direct Path | code | yes | 4 | 3 | 1 (self-review) |
| Spec | Feature Factory | spec.md | yes | 6 | 6 | 2 |
| Plan | Feature Factory | plan.md | yes | 10 (6 r1 + 4 r2) | 9 | 2 |
| Tasks | Feature Factory | tasks.md | n/a — not reached | — | — | — |
| Implement | Feature Factory | code | n/a — not built | — | — | — |

FF reviews genuinely changed its spec and plan (real, CODE-CONFIRMED findings). But the question that matters is whether they caught anything the **Direct build shipped wrong**:

| FF finding | Severity | Did Direct ship it wrong? |
|---|---|---|
| Long-poll pins the request-scoped DB connection across the hold → pool exhaustion | HIGH | **No** — Direct's self-review caught it (it surfaced as a test-suite slowdown), fixed via `db.rollback()` before waiting + per-tick short-lived sessions. |
| `turns_played` misattributed if keyed off `served_by_connection_id` | HIGH | **No** — Direct credited the *submitting* connection (`require_connection`) with an atomic SQL `+1` from the start. |
| Play-prompt must teach two-phase talk→act + resend `agent_turn_token` | MED | **No** — Direct's `setup-mcp.md` covers the talk phase + token. |
| Counter write-amplification at `mark_seen` | MED | **No** — Direct folded the counter into the existing throttled heartbeat UPDATE. |
| Re-validate **disabled-user** state mid-hold (not just deletion) | MED | **Yes (minor)** — Direct re-checks deletion per tick but not disabled-user/paused; ≤25s window, self-corrects on next poll. **The one real thing FF uniquely caught.** Filed as a follow-up. |
| Long-poll should be opt-in (default off) | — | **Direct did BETTER than FF here** — Direct made `hold_seconds` opt-in so existing callers/the connector are untouched; the FF *plan* was default-on (the worse choice). |

## Token Efficiency

| Path | Billed Input | Cache Read | Output | Real-Work (billed+output) |
|------|-------------|-----------|--------|--------------------------|
| Direct Path | — | — | — | ~211,703 (subagent total; ~30 min, 155 tool uses) |
| Feature Factory (reviews only) | Codex 374,891 + Gemini 130,392 | — | Gemini 2,900 | ~508,183 review-only |

**Caveats (both directions):** Direct's number is the subagent's lump total (not split). FF's number is **review-only (Codex + Gemini)** and **excludes the large Claude orchestrator tokens** spent this session authoring spec/plan and reconciling 4 review rounds — so FF's true cost is well above the figure shown. FF **also has not built its code yet** (tasks + implement + diff-review would add substantially more). Even understated, FF cost ≫ Direct.

## Outcome

- **Did FF catch problems Direct missed?** One minor one (disabled-user mid-hold revalidation, ≤25s self-correcting). It did **not** catch any of the critical bugs in Direct's shipped code — because Direct didn't have them.
- **Did the reviews change the code?** They changed FF's *spec/plan* materially, but the Direct build independently reached an equivalent-or-better implementation — and made the better core design choice (opt-in long-poll) that FF's plan got wrong.
- **Was the overhead worth it here?** No. The dominant risk (DB pinning) was **not silent** — it slowed the test suite, so one self-review surfaced it. FF's heavy rounds pay off for *silent* correctness bugs that pass tests; this feature had essentially one minor silent gap.
- **Which path next time?** For hot-path backend work where the main risks are perf/concurrency that manifest in the test suite, a competent Direct build + one self-review matches FF at a fraction of the cost. This **contradicts the standing "backend → Feature Factory" lean** — the better predictor is *silent vs. test-visible risk*, not backend-vs-UI.
