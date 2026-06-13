---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/byo-terminal-mode-a/plan.md"
artifact_sha256: "81641ce86d8582a9feaad1866480df16a0b7a0ee9434b8628d98e7de6a641ad6"
repo_root: "."
git_head_sha: "2f67a923b1ce93b73459ff683ae2f4f3e3e5c504"
git_base_ref: "origin/main"
git_base_sha: "c9d01a3e4d1e90198936d568835b6ed2609bcc6f"
generation_method: "gemini-cli"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/byo-terminal-mode-a/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **DB Connection Exhaustion (HIGH):** The plan correctly identifies that `next_turn` cannot hold a request-scoped `DbSession` during a 25s long-poll, but its proposed fix—opening and closing sessions *per tick* inside a single handler—is highly dangerous in a high-concurrency FastAPI environment. This pattern introduces significant overhead and complex lifecycle management for connection pooling. Furthermore, if a `db: DbSession` dependency is dropped, any existing logic (like `require_agent_player` or `require_connection`) that relies on injecting a `DbSession` will break, necessitating a cascade of refactoring across `app/deps.py` and potentially other route dependencies. [CODE-CONFIRMED] (Ref: `app/deps.py` shows `get_db` dependency injection is pervasive; `app/db.py` handles the scoped session lifecycle).

2.  **Concurrency Race in `turns_played` (HIGH):** While the plan calls for a "SQL-level atomic update" for `turns_played`, it relies on the `submitting connection` resolved by `require_connection`. However, if the sticky-pin (the connection currently "serving" the match) is changed by the platform's scheduler (e.g., due to a stall or failover) *exactly* when a `submit_action` is in flight, the submitting connection might be different from the one that the system *intends* to credit, or the submission might be accepted by an "eligible" connection that is not the one pinned. The plan treats the submitting connection as the single source of truth for credit, which might drift from the internal match state. [CODE-CONFIRMED] (Ref: `app/engine/turn_routing.py` and `app/models/player.py` confirm stickiness is a dynamic property managed by the engine, not fixed).

3.  **Heartbeat Write Amplification (MEDIUM):** The plan suggests updating `api_call_count` within the *same throttled update* as `last_seen_at` in `mark_seen`. If `mark_seen` is triggered by a polling connection (which happens very frequently in Mode A), and if the throttle window is long, this couples "read-activity" (polling) with "write-persistence" (updating the row). If a polling client is aggressive, this could lead to contention on the `connections` table, especially if the SQLite back-end (standard for local development/testing) locks the table on writes. [CODE-CONFIRMED] (Ref: `app/engine/connection_activity.py` performs these writes; SQLite locks table/db on write).

## Residual Risks

*   **Pool Starvation:** Even with `async with` session management, the underlying SQLAlchemy connection pool can be exhausted if the long-poll loop ticks faster than connections can be returned, especially if the async loop context isn't carefully managed.
*   **Logic Drift:** By separating turn credit from the sticky-pin logic, the system risks having a `turns_played` count that does not align with the number of turns assigned to a connection in the engine's view of the world (e.g., if a connection submits a turn but fails to finalize, or vice versa).
*   **Mode A vs. Connector Mismatch:** The plan assumes the Mode A long-poll can be safely added to the existing `next_turn` endpoint without affecting the connector's performance. Any delay in the "no-turn" branch risks impacting the connector's tight loop, which expects high-frequency, low-latency polling.

## Token Stats

- total_input=24349
- total_output=773
- total_tokens=25122
- `gemini-3.1-flash-lite`: input=24349, output=773, total=25122

## Resolution
- status: open
- note: