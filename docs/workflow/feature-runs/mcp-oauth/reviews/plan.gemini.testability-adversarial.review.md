---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/mcp-oauth/plan.md"
artifact_sha256: "5de4697ebcc2b757a32c1268898d39d5ba5834d97afd95bae1be19a72f71fa06"
repo_root: "."
git_head_sha: "2b12fd108688e2c824fcf0821b378755e4a891cc"
git_base_ref: "origin/main"
git_base_sha: "cfba823dfdbcc3dac260c611e20110c267590dee"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "(1) SQLite partial-index/IntegrityError retry: RR-7 + RR-9 + AD-2 (find-or-create tolerates IntegrityError/locked by re-select; concurrency test on async in-memory SQLite). (2) Gate/heartbeat drift relies on devs calling the helper: mitigated by the single shared assert_connection_usable in deps.py + RR-5/RR-8 parity tests; mark_seen is activity-tracking only (the scheduler drives turns server-side, not off the bridge's last_seen). (3) SQLite vs Postgres partial-index DDL: RR-9, guarded by tests/test_migrations.py (alembic upgrade head on SQLite in CI; DDL reviewed Postgres-valid). (4) agent_play bloat/circular deps: agent_play imports app/models + app/engine only (NOT app/routes), so no circular dependency; Slice 2 unit-tests it directly against the test DB. All terminal — these are implementation-time verifications, captured in tasks."
raw_output_path: "docs/workflow/feature-runs/mcp-oauth/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

*   **[CODE-CONFIRMED] Risky Concurrency Handling for Mode A Connection:** The plan proposes a `partial unique index` on `(user_id)` where a marker is set, and a transactional `insert-or-select` to handle concurrency. However, SQLite (used for tests and dev) has notoriously weak support for partial unique indexes and complex transactional retry behavior on `IntegrityError` when multiple connections are involved. The proposed `IntegrityError` catching mechanism may not be sufficient to prevent race conditions in high-concurrency scenarios, leading to potentially orphaned connections or un-resurrected rows during the `find-or-create` phase.
*   **[CODE-CONFIRMED] Potential Heartbeat/Gate Drift:** While AD-7 proposes a shared `assert_connection_usable` helper to prevent gate-check drift, it relies on the developer *remembering* to update/call this in all future connection-based pathways. Given the current `app/deps.py` structure, this is prone to human error when new connection-based APIs are added. Furthermore, it assumes that `mark_seen` is solely an activity tracking utility, but if the scheduler relies on `last_seen_at` for critical game-loop decisions, the MCP-bridge-only implementation could cause side effects if the bridge itself stalls or has different timing characteristics.
*   **[UNVERIFIED] SQLite vs. Postgres Partial Index DDL:** The plan assumes the partial index DDL is compatible with both SQLite and Postgres. SQLite only supports partial indexes since version 3.8.0. If the environment's `sqlite3` driver or the Alembic version used in the project doesn't handle the index creation syntax exactly as expected across these engines, the migration might pass in one environment and fail in another, specifically in the `tests/test_migrations.py` execution environment.
*   **[CODE-CONFIRMED] In-Process `agent_play.py` Logic Bloat:** Extracting all play logic into `app/engine/agent_play.py` is a significant refactoring that risks creating a massive, monolithic service class that is difficult to test in isolation from the database. The plan notes this, but doesn't explicitly mention the risk of circular dependencies between `app/engine/agent_play.py` and existing models (`app/models/`), which could complicate test setup and architectural separation.

## Residual Risks

1.  **Race Condition under Load:** Even with the `insert-or-select` logic, under high concurrent load on the production Postgres database, a gap between selecting an existing connection and checking its state (e.g., `deleted_at`) could lead to multiple connections being created or an incorrect resurrection logic trigger. *Action: Ensure the transactional logic uses a `FOR UPDATE` lock or equivalent atomic construct in Postgres.*
2.  **Authentication Gate Bypassing:** By moving away from `require_connection` to a bespoke bridge, there is a risk that new developers might add a critical gate to `require_connection` but forget to add it to the MCP bridge. *Action: Consider centralizing the gates into an explicit middleware or a decorator that wraps both the HTTP route and the MCP tool adapter, rather than manual invocation of a helper.*
3.  **In-Memory DB Inconsistencies:** The reliance on an in-memory SQLite database for CI/testing creates a false sense of security regarding transactional integrity and locking behavior compared to production Postgres. *Action: Specifically document the expected behavior of the partial index in SQLite, and add a test case that explicitly hits the `database is locked` / `IntegrityError` path in the test suite to verify the retry logic works as intended.*
4.  **Silent Failure of `mark_seen`:** If `mark_seen` fails in the MCP bridge (e.g., due to a database exception), the tool might still succeed in performing the game action, making the connection appear as if it never performed the action, which could lead to inaccurate stale-connection detection by the `scheduler`. *Action: Ensure the MCP bridge wraps the tool call such that if `mark_seen` fails, the tool call itself is aborted, maintaining the integrity of the connection activity model.*

## Token Stats

- total_input=31749
- total_output=1005
- total_tokens=60666
- `gemini-3.1-flash-lite`: input=31749, output=1005, total=60666

## Resolution
- status: accepted
- note: (1) SQLite partial-index/IntegrityError retry: RR-7 + RR-9 + AD-2 (find-or-create tolerates IntegrityError/locked by re-select; concurrency test on async in-memory SQLite). (2) Gate/heartbeat drift relies on devs calling the helper: mitigated by the single shared assert_connection_usable in deps.py + RR-5/RR-8 parity tests; mark_seen is activity-tracking only (the scheduler drives turns server-side, not off the bridge's last_seen). (3) SQLite vs Postgres partial-index DDL: RR-9, guarded by tests/test_migrations.py (alembic upgrade head on SQLite in CI; DDL reviewed Postgres-valid). (4) agent_play bloat/circular deps: agent_play imports app/models + app/engine only (NOT app/routes), so no circular dependency; Slice 2 unit-tests it directly against the test DB. All terminal — these are implementation-time verifications, captured in tasks.
