---
reviewer: "claude"
lens: "testability-adversarial"
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
raw_output_path: "docs/workflow/feature-runs/dedup-engine-cseries/reviews/plan.claude.testability-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

**[major]** **C8 site count is wrong — the plan says "6 inline sites" but there are 7.** [CODE-CONFIRMED] `rg "state = GameState.CANCELLED"` finds inline CANCELLED transitions at `scheduler.py:184, 319, 400` (3), `arena.py:188, 300, 331` (3), and `scheduler_turn_loop.py:214` (1) = **7 sites**, plus the `match_deletion.cancel_match` def. The plan (module-homes table) and the disposition-tracking section both assert the presence check covers "all 6 inline sites." A presence check calibrated to 6 will pass while leaving the 7th site un-converted. The C8-cancel test/check must assert the exact converted-site list, not a count, and the count must be corrected to 7.

**[major]** **C8 verification regex passes even if a site keeps a stale/duplicated `now`.** [CODE-CONFIRMED] `arena.py:189` uses `existing.cancelled_at = datetime.now(timezone.utc)` inline (a fresh `now`), unlike the sibling arena sites that use a captured `now`. The `rg "state = GameState.CANCELLED"` check catches a missed `state=` line but does not verify each converted call passes the same `now` it previously used. The C8-cancel test needs a positive assertion that `mark_cancelled(match, now)` sets BOTH fields to the passed `now`, and at least one site-level assertion that a converted caller's `cancelled_at` equals its own captured/fresh `now`.

**[major]** **C4 helper signature must toggle the reserved filter, and the watchdog's value is unnamed.** [CODE-CONFIRMED] The watchdog (`scheduler.py:313-317`) counts `left_at.is_(None)` only — it *includes* reserved seats. `_active_player_count` (`scheduler.py:95-112`) filters `left_at.is_(None) AND seat_reserved_until.is_(None)` — confirmed only. So `active_player_count(..., exclude_reserved)` must mean: `True` → confirmed (add reserved filter), `False` → left-only (watchdog/seated). The plan never states the watchdog uses `exclude_reserved=False`; without that, "grep each call site for its keyword" can't tell right from wrong. The C4-watchdog test must assert a held-seat (reserved) ACTIVE game is NOT cancelled by `_watchdog` (its count includes the reserved seat) — opposite polarity from the start-floor sites.

**[minor]** **C6 importer claim is refuted; per-site window constant must be pinned.** [CODE-CONFIRMED] `provider_readiness.py:20-23` already imports `_connection_is_live` from `connection_health_badge`. And `provider_loop_running` (`provider_readiness.py:198-223`) inlines its own window math against `LOOP_RUNNING_WINDOW_SECONDS` — a DIFFERENT constant than `_within_window`'s `LIVE_WINDOW_SECONDS` callers. Routing it through `within_window` is a behavior change unless the same constant is passed. The verification must assert `provider_loop_running` still keys off `LOOP_RUNNING_WINDOW_SECONDS`, not just the PAUSED short-circuit.

**[minor]** **C5 `_has_moved` is not byte-identical, and no test pins the equivalence.** [CODE-CONFIRMED] `connection_activity.py:80` (`bot_id`, builds `stmt`, `.first() is not None`) vs `agent_onboarding.py:127` (`agent_id`, inline select, `row is not None`) are semantically equal but textually different. The C5 test targets the constant/precedence; add a `has_moved` test covering the defaulted-vs-real submission boundary so the unified function is pinned.

**[minor]** **Fast-lane command would skip the new tests.** [CODE-CONFIRMED] New C4/C5/C8/C2 characterization tests boot the in-memory DB and will be auto-tagged `integration`, so `pytest -q -m "not integration"` excludes them. Each test-bearing slice must run the full `pytest -q` or explicitly target `pytest tests/test_<name>.py`, not the fast lane.

## Residual Risks

**[major]** **C2's `current_round`-write divergence must be caught by the tests, not just the reviewer.** [CODE-CONFIRMED] `_open_turn` sets both `current_round` and `current_turn`; `_open_actor_turn` sets only `current_turn` and has no resume guard. The residual-risk "reviewer confirms exactly 3 axes" can rubber-stamp a merge that silently writes `current_round` from the sequential driver. C2-seq must assert the sequential path leaves `current_round` untouched; C2-sim must assert get-or-create on resume — so the tests fail-on-wrong-merge for this axis. Given the divergence, default the disposition gate to `not-a-true-duplicate`.

**[minor]** **C2-sim test writability through the loop is unverified.** [UNVERIFIED] `_open_turn` runs inside `_run_game` with real `asyncio.sleep` waits and `publish`/bot-auto-submit side effects. The plan should state whether C2-sim calls the opener directly (writable) or drives the loop (needs mocks named).

**[minor]** **Collected-count baseline is a weak wrong-merge guard.** [UNVERIFIED] Count parity proves no test was deleted, not that the new tests exercise the refactored path. Require each medium slice to record the new test failing against a deliberately-wrong merge (red-then-green), per spec criterion 4.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Findings incorporated into plan.md: C8=7 sites + assignment-scoped presence check; C2 defer-expected; C4 exclude_reserved per site (watchdog False) + two calls in fill_match_with_bots; C6 keep LOOP_RUNNING_WINDOW_SECONDS + per-row loop + PAUSED guard; C5 connection_health shim + agent_idle rename + has_moved test; full pytest for DB-backed char tests + red-then-green; C3 import-edge smoke. See plan round-1 reconciliation table.
