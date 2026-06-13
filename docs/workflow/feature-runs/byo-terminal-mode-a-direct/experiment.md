# Experiment run: BYO Terminal Mode A (interactive MCP play) v1 — DIRECT PATH

Arm: Direct Path (build it well, one structured self-review, stop). No
spec/plan/tasks ceremony, no adversarial review rounds.

| Stage | Artifact | stage_started_at | stage_finished_at | artifact_before_sha256 | artifact_after_sha256 | review_rounds | issues_raised | issues_accepted | artifact_revised |
|-------|----------|------------------|-------------------|------------------------|-----------------------|---------------|---------------|-----------------|------------------|
| Implement | code | 2026-06-13T07:16:20Z | 2026-06-13T07:44:20Z | 483e5572e03e93bfe4eaacf54148c96f770e1b796984956b7b7785a12ae7e694 | 4f08b132a4ba8f83bf88f72530f4d9ad50eedea32371e4c910c3ea3b03b7dd5b | 1 | 4 | 3 | yes |

## Self-review (single structured pass)

Checklist applied: (i) acceptance criteria, (ii) correctness/scope risk, (iii)
missing tests, (iv) stale/confusing wording.

Issues raised (4) / accepted (3):

1. (ii, correctness) **Long-poll DB-connection hold** — initial draft held the
   long-poll loop open while the bounded-default was 25s, which (a) pinned the
   request-scoped DB connection idle across the wait and (b) made ~10 unrelated
   tests block 25s each, blowing the suite from 22s to 98s and tripping a
   pre-existing wall-clock-fragile test (`test_coverage_health_and_join_gate.py`
   bakes `datetime.now()` into module-level constants). ACCEPTED. Reworked the
   long-poll to be OPT-IN via `hold_seconds` (default 0 = immediate return), so
   the connector and every existing caller keep old behaviour; only the MCP
   `get_next_turn` tool requests the hold. Also explicitly `db.rollback()` to
   release the request-scoped connection before waiting and re-load the
   connection inside each fresh per-check session.
2. (ii, correctness) **Usage-counter double-count** — hand-assigning
   `bot.api_call_count` after a relative `col + 1` UPDATE marked the ORM object
   dirty, so a later commit in the same request wrote the count a second time
   (observed count = 2 after one call). ACCEPTED. Replaced the hand-assignment
   with `db.refresh(bot)` so the in-memory object reflects exactly what the
   atomic UPDATE wrote.
3. (iii, tests) **Missing no-hold / plural cadence coverage** — added
   `test_no_hold_returns_waiting_immediately_with_idle_cadence` asserting the
   default path returns instantly with the 30s idle hint on both singular and
   plural endpoints. ACCEPTED.
4. (iv, wording) **Stale comment** — `_LIVE_WINDOW_SECONDS` comment referenced a
   "~40s long-poll"; corrected to ~25s. ACCEPTED (folded with #3 into the revise).

Not accepted: dropping `db.refresh(bot)` to save one PK SELECT on the auth hot
path — kept the refresh for correctness/future-proofing (downstream readers of
the connection object see fresh `status`/`first_connected_at`); the extra
indexed SELECT is cheap.

## Validation

Preflight gate (run from worktree root), all green:

- `python3 -m ruff check .` → All checks passed!
- `mypy app/ mcp_server/` → Success: no issues found in 110 source files
- `python3 -m pytest -q` → 754 passed in 24.82s

Migration added: revision **0031** (`0031_connection_usage_counters.py`,
down_revision 0030).

Session JSONL: /Users/chrislaw/.claude/projects/-Users-chrislaw-hoard-hurt-help/cf63f5a9-5058-4146-b0e9-df2115f366a6.jsonl
