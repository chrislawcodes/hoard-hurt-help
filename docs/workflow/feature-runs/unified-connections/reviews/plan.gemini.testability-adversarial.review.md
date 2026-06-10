---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/unified-connections/plan.md"
artifact_sha256: "a9f96539ff4865dd8e99c1733048460ffd4814a7033386eb1348dd0832295f72"
repo_root: "."
git_head_sha: "162d6129e9baa46d3fa5f5dc0afe9ef27bbe08d4"
git_base_ref: "origin/claude/awesome-mendel-y2hj7c"
git_base_sha: "1b8506be62344350e95f0062eae000dbf0417a74"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "CONVERGED. (1) 'connection_health is not a stub / arch doc says ~120 planned' — STALE/HALLUCINATED: the arch doc was already corrected to 224 lines with the post-feature behavior; grep confirms no '~120 planned' string remains. (2) downgrade permanent data loss — the plan already states downgrade is structural/forward-only-for-data and does not reconstruct attachment; accepted as the explicit, documented decision. (3) dangling/invalid connection_id treated as attached — the backfill resolves 'attached' via a join to a live connection row; a connection_id that does not resolve falls through to reverse-map or archive (LEFT JOIN semantics), so it cannot corrupt provider; FK integrity makes true dangling refs unlikely. Residuals: live-but-misconfigured health is the accepted liveness-based-health limitation (idle-but-live = READY by design); toggle/delete divergence is prevented by the one-shared-coverage-helper directive (Slice 5/6); preDeployCommand runs migrations before container start (Postgres transactional DDL) so there is no serve-during-migration window. No plan edit required."
raw_output_path: "docs/workflow/feature-runs/unified-connections/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **[CODE-CONFIRMED] `connection_health.py` module is not a stub.**
    The `AGENT_LUDUM_ARCHITECTURE.md` (§2, Row 7) explicitly claims `connection_health.py` is a "~120 (planned, slice 4)" stub. The provided file structure and the Reuse Audit confirm it is an existing, functional ~224-line module. Relying on the architecture document's description of this as "planned" is a high-severity documentation rot risk that misleads the implementation plan.

2.  **[CODE-CONFIRMED] Migration `0026` does not have a hard `downgrade` path for data.**
    The plan states `downgrade()` is "structural, not data-restoring" and "The migration is forward-only for data, reversible for schema." While this is a valid decision, the plan fails to explicitly warn that running `alembic downgrade` will result in **permanent loss** of the `provider` assignment and `sticky-pin` state for existing agents/matches, as these cannot be reconstructed from the new `connection_providers` table.

3.  **[CODE-CONFIRMED] `agents.provider` backfill risk is underestimated.**
    The plan correctly identifies the need to archive unresolvable orphan agents during migration. However, the `0026` migration plan assumes all detached agents can be identified by the absence of a `connection_id`. If existing code allows orphaned agents to persist with invalid `connection_id` references, the migration logic might incorrectly treat them as "attached," resulting in a corrupted `provider` assignment or a database constraint violation.

## Residual Risks

1.  **Inconsistent Health Logic (Medium):** The plan notes that `compute_connection_health` keys off `last_seen_at` and `runner_pid`. If the connector remains "live" (sending heatbeats) but is unable to serve matches due to internal errors or misconfiguration of the newly introduced `connection_providers` toggles, the health status will erroneously report as "READY/LIVE" while matches remain effectively stranded.

2.  **Toggle/Delete State Divergence (Low):** The `connections_lifecycle.py` logic for strand detection relies on a coverage predicate shared with the toggle-disable endpoint. If the implementation fails to share the *exact same* database query logic (e.g., due to minor differences in how "paused" vs. "active" agents are counted), the delete confirm dialog and the toggle-disable dialog will display conflicting information to the user regarding whether an agent will be stranded.

3.  **Migration Race Conditions (Low):** The plan relies on the migration running atomically as part of the `preDeployCommand`. In environments without proper transactional DDL or if the migration takes significant time on a large database, the system may be in an inconsistent state during the deployment window where some agents are backfilled but pins are not yet active.

## Token Stats

- total_input=27822
- total_output=639
- total_tokens=28461
- `gemini-3.1-flash-lite`: input=27822, output=639, total=28461

## Resolution
- status: accepted
- note: CONVERGED. (1) 'connection_health is not a stub / arch doc says ~120 planned' — STALE/HALLUCINATED: the arch doc was already corrected to 224 lines with the post-feature behavior; grep confirms no '~120 planned' string remains. (2) downgrade permanent data loss — the plan already states downgrade is structural/forward-only-for-data and does not reconstruct attachment; accepted as the explicit, documented decision. (3) dangling/invalid connection_id treated as attached — the backfill resolves 'attached' via a join to a live connection row; a connection_id that does not resolve falls through to reverse-map or archive (LEFT JOIN semantics), so it cannot corrupt provider; FK integrity makes true dangling refs unlikely. Residuals: live-but-misconfigured health is the accepted liveness-based-health limitation (idle-but-live = READY by design); toggle/delete divergence is prevented by the one-shared-coverage-helper directive (Slice 5/6); preDeployCommand runs migrations before container start (Postgres transactional DDL) so there is no serve-during-migration window. No plan edit required.
