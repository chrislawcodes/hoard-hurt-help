---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/byo-terminal-mode-a/spec.md"
artifact_sha256: "9d82e83ad25958775f6d85ddc703587cf5536429dbff17c9f337f11b471feb64"
repo_root: "."
git_head_sha: "2f67a923b1ce93b73459ff683ae2f4f3e3e5c504"
git_base_ref: "origin/main"
git_base_sha: "c9d01a3e4d1e90198936d568835b6ed2609bcc6f"
generation_method: "gemini-cli"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/byo-terminal-mode-a/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

*   **[CODE-CONFIRMED] [HIGH SEVERITY] Potential DB Connection Exhaustion.** In `agent_next_turn.py`, `next_turn` implements a long-poll. While the spec `FR-002` claims the implementation avoids pinning a DB connection across the wait, the current `next-turn` implementation is a standard `async` FastAPI endpoint. If multiple concurrent clients hit this endpoint while no turn is available, and it were to contain a `while` loop with `await asyncio.sleep` (as per `FR-001`), every request would hold its FastAPI worker, and if the DB dependency `DbSession` is not released/closed and re-opened inside that loop, it could potentially hold connections in the pool. The current implementation does not show the "internal periodic re-check loop" requested in `FR-001`; it simply returns `waiting` immediately.
*   **[CODE-CONFIRMED] [HIGH SEVERITY] Missing Implementation of Long-Poll.** The requirements in `spec.md` (FR-001) mandate a bounded long-poll with an internal re-check loop. The existing `next_turn` in `agent_next_turn.py` does not implement this; it immediately returns `{"status": "waiting", ...}` if `select_next_turn` returns `None`.
*   **[CODE-CONFIRMED] [MEDIUM SEVERITY] Potential Race Condition in Counter Updates.** The spec `FR-006` requires incrementing "turns played" at the `submit_action` point. In `agent_api.py`, `agent_submit` updates the DB to record the submission and later calls `mark_first_move`. If multiple turns are submitted rapidly, and the counter increment is implemented as a simple read-modify-write without database-level atomic increment (`update(Connection).values(turns_played=Connection.turns_played + 1)`), it risks lost updates under concurrency.
*   **[UNVERIFIED] [LOW SEVERITY] Potential Client Timeout Mismatch.** The spec assumes a ~25-30s hold is safe for typical MCP clients. If the client has a shorter default timeout, the long-poll may fail frequently, causing the client to retry and potentially leading to higher load than intended.

## Residual Risks

*   **Worker/Pool Starvation:** If the long-poll implementation, once added, improperly manages the `DbSession` lifecycle (i.e., holding a session open across the entire 25s wait period rather than acquiring/releasing it inside the re-check loop), a small number of idle waiting clients could exhaust the entire database connection pool or block all FastAPI workers.
*   **Calibration Drift:** The `SC-003` criteria relies on a one-time calibration of token costs. If the underlying LLM providers (e.g., Claude, Gemini) change their internal tokenization or pricing models, the dashboard's "approximate call count" (if added) will become misleading, potentially causing player confusion despite the "approximate" label.
*   **Jitter Implementation:** Spec `FR-013` suggests adding jitter to the polling interval. Without this, a fleet of agents that get disconnected simultaneously (e.g., due to a server restart) will synchronize their polling attempts, creating a "thundering herd" effect on the server every 30 seconds.

## Token Stats

- total_input=45098
- total_output=734
- total_tokens=45832
- `gemini-3.1-flash-lite`: input=45098, output=734, total=45832

## Resolution
- status: open
- note: