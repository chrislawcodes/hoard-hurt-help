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
