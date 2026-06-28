---
reviewer: "claude"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/agent-model-selection/plan.md"
artifact_sha256: "a8277cafd8898cb43c53be4dd4dab86c93fe2cfdeb3fc95bd42923e2f111664c"
repo_root: "."
git_head_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
git_base_ref: "origin/main"
git_base_sha: "ee8138143e38aef51b57796968d6bf2f5d5e3737"
generation_method: "claude-subagent"
resolution_status: "open"
resolution_note: ""
raw_output_path: "docs/workflow/feature-runs/agent-model-selection/reviews/plan.claude.testability-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

**1. [MEDIUM] [CODE-CONFIRMED] SC-001's ~60s cadence had only a manual log-grep verification — no pytest assertion.** Make the cadence decision a pure predicate `_should_verify(now, last_verify)` unit-tested like `_poll_failed`/`_phase_time_budget`, so CI guards the mechanism.

**2. [MEDIUM] [CODE-CONFIRMED] Migration 0045 had no `downgrade`, but `test_sqlite_migrations_round_trip` runs `downgrade base`.** Add the `drop_table` downgrade.

**3. [MEDIUM] [CODE-CONFIRMED] FR-009a "captured real stderr samples" aren't runnable — no fixtures exist and Gemini CLI is dead.** Commit synthetic stderr fixtures (synthetic Gemini sample) and test the classifier against them.

**4. [LOW] [CODE-CONFIRMED] The play-time status-flip (reason → flips `(conn,provider,model)` to failed/timeout) needs its own named end-to-end test — it's the exact silent-failure class the feature kills.**

**5. [LOW] [CODE-CONFIRMED] FR-013 timeout-escalation boundary (2 → timeout, 3 → failed) needs a named off-by-one test.**

## Residual Risks

- Cross-process classifier can diverge (connector copy vs server `record_results`); pin both with one shared parametrized `(exit,stderr)→status` table.
- The FR-014 join-warning union depends on a new read path on the join hot path; the `model_status_for` union test (any verified ⇒ runnable; MCP/paused excluded) should be required, not optional.
- Gemini classification can't be verified against reality here; document the synthetic sample explicitly.

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: open
- note: 