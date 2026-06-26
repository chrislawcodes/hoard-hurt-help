---
reviewer: "claude"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/dedup-engine-cseries/plan.md"
artifact_sha256: "44611cac24d5f3dcee2ce451f916ff4db298418340a12d303934e0faf0a63912"
repo_root: "."
git_head_sha: "21184fbbb85c8009389ffc42a1d750ea586359a6"
git_base_ref: "origin/main"
git_base_sha: "9d36fdc28273b44ec7b04fbdaf747b1b9f18c221"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Findings incorporated into plan.md: C8=7 sites + assignment-scoped presence check; C2 defer-expected; C4 exclude_reserved per site (watchdog False) + two calls in fill_match_with_bots; C6 keep LOOP_RUNNING_WINDOW_SECONDS + per-row loop + PAUSED guard; C5 connection_health shim + agent_idle rename + has_moved test; full pytest for DB-backed char tests + red-then-green; C3 import-edge smoke. See plan round-1 reconciliation table."
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/plan.claude.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

**[major]** C8 site count is wrong, which under-scopes the slice and breaks the presence-check gate. Actual field-mutation sites (`state = GameState.CANCELLED` + `cancelled_at = ...`) are: `scheduler.py:184,319,400` (3), `arena.py:188,300,331` (3), `scheduler_turn_loop.py:214` (1) = **7** inline sites, plus `match_deletion.py:41` (`cancel_match`). [CODE-CONFIRMED]. The plan's "6 inline sites" and the Residual-Risk grep `rg "state\s*=\s*GameState.CANCELLED" app/engine/` are wrong: the count is off by one AND the regex also matches `state_machine.py` transition-table literals (`GameState.SCHEDULED: {… GameState.CANCELLED}`) and membership tests (`state in (…, GameState.CANCELLED)`) in `join_gate_capacity.py`/`match_deletion.py`/`agent_play.py`, which are NOT assignments and must not be rewritten. The presence check as written produces false positives.

**[major]** C2's "3-axis opener" is insufficient — the openers differ structurally, not by parameter. `_open_turn` (`scheduler_turn_loop.py:281`) is a **get-or-create** (queries existing Turn, returns unchanged on resume, INSERTs only when absent, `:290-303`); `_open_actor_turn` (`turn_drivers.py:192-211`) is a **blind INSERT** with no resume idempotency. [CODE-CONFIRMED]. Resume is a query-and-branch code path, not a boolean. Folding it either silently adds get-or-create to the sequential driver or leaves a hidden 4th behavior. Plus the `set_current_round` scope diff and now-source diff. The disposition gate exists, but the evidence says `not-a-true-duplicate` should be the EXPECTED outcome, not the exception.

**[minor]** C6 home/importer claim is imprecise and the import is non-trivial. `provider_readiness.py:22` already imports `_connection_is_live` from `connection_health_badge` (so "imports nothing from health_badge" is false). And `provider_loop_running` (`provider_readiness.py:186-223`) does NOT call `_within_window` today — it inlines a per-row None-skip + tz-coercion + `<= LOOP_RUNNING_WINDOW_SECONDS` loop. Routing it through `within_window` is a real edit with a (small) behavior surface using a DIFFERENT constant — not the "trivial delegate" the slice-3 "Low risk" label implies. `_connection_is_live`'s PAUSED/None guards and `provider_loop_running`'s per-row None-skip loop must stay.

**[minor]** C5: of the three importers, only `agent_idle.py` uses a different name (`_UPCOMING_STATES` vs `_PREGAME_STATES`), so it gets a rename + import. More importantly, `connection_activity.py:25-33` imports liveness symbols via the **`connection_health.py` re-export aggregator**, not directly. The plan never mentions this shim: if promoted symbols (`has_moved`, `within_window`) are expected to remain importable via the aggregator, `connection_health.py`'s `__all__` (`:72,81,92`) must be updated. [CODE-CONFIRMED the shim exists].

**[minor]** C4 `fill_match_with_bots` needs the helper called **twice**, which the "4 standalone count sites" framing obscures. `arena.py:98-115` runs two distinct counts in one function: `confirmed` (left+reserved) and `seated` (left only). [CODE-CONFIRMED]. `active_player_count(..., exclude_reserved)` expresses both, but the slice must rewire that one function with two calls, and the per-site keyword grep must expect two keywords there.

## Residual Risks

- **C2 disposition is a coin-flip the tests must arbitrate.** Given get-or-create vs blind-INSERT, treat `not-a-true-duplicate` as the expected landing; C2-seq/C2-sim must assert resume idempotency for the simultaneous path and its absence for the sequential path.
- **The C8 presence-check regex is too broad and will mismatch.** Needs an assignment-only pattern scoped to the known mutation files, or it masks a missed site. [CODE-CONFIRMED].
- **C3 cycle is clean today; the smoke test must include the NEW edge** (`turn_drivers` importing `user_match_start.is_bot_kind`), not just bare module imports, since the cycle would only appear once the import is added. [CODE-CONFIRMED clean today].
- **Slice ordering creates no broken intermediate, one caveat:** if C2 defers, the planned C2 now-source standardization onto `turn_clock.now_utc()` silently drops, leaving `_open_actor_turn` on `_now()`. Not a regression (status quo), but the plan should note deferral forgoes it. [CODE-CONFIRMED the openers use different now-sources today].

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Findings incorporated into plan.md: C8=7 sites + assignment-scoped presence check; C2 defer-expected; C4 exclude_reserved per site (watchdog False) + two calls in fill_match_with_bots; C6 keep LOOP_RUNNING_WINDOW_SECONDS + per-row loop + PAUSED guard; C5 connection_health shim + agent_idle rename + has_moved test; full pytest for DB-backed char tests + red-then-green; C3 import-edge smoke. See plan round-1 reconciliation table.
