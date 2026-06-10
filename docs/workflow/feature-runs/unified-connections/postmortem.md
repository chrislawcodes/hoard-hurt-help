# Post-mortem: unified-connections

## What went well

- **Adversarial spec/plan review earned its keep.** Across spec (4 rounds) and
  plan (5 rounds), Codex/Gemini caught genuinely load-bearing issues that
  changed the design: the migration crashing on `AgentKind.BOT` rows
  (NOT-NULL provider), `resume_agent` rejecting every agent after the column
  drop, `agent.provider` drift across three model-edit paths, and the conflict
  between a loud-fail migration and Railway's auto-`preDeployCommand` (resolved
  by archiving orphans instead of aborting). These were design-stage fixes, not
  production bugs.
- **The reuse audit prevented duplication** (extend `select_next_turn`,
  `PROVIDER_MODELS`, `connection_health`; only `connection_providers`, the pin
  columns, and `turn_routing.py` were genuinely new) and caught two spec
  inaccuracies (no server-side delete-confirm pattern existed; `connection_health`
  was a real module, not a stub).
- **Per-slice Preflight Gate** caught real integration breaks early (the
  nullable-provider type mismatch in slice 1; every test fixture that needed a
  `connection_providers` row), keeping each slice green before its diff review.

## What didn't work

- **The runner's `implement` (codex exec) timed out at the 3600s ceiling on
  every complex route slice (3, 4)** with no commit — the prompt bundles the full
  spec+plan as context, and gpt-5.4-mini thrashed on large slices. Slices 3–8
  were ultimately implemented by hand. This is the single biggest cost of the run.
- **The diff checkpoint wedges on new-file slices.** `run_gemini_review` hashes
  the new-file-*expanded* patch while the health check hashes the *raw* patch on
  disk — they never match, so the stage stays "repairable." Worked around by
  writing the expanded patch to disk then `--use-existing-artifact`.
- **The codex review default 120s timeout** is too low for large spec/plan
  artifacts and isn't exposed by the checkpoint runner — required generating the
  codex review manually at 300–540s, then letting repair skip the healthy review.
- **`scope.json` didn't tolerate `docs/workflow/operations/review-attempts.jsonl`**
  (appended by every command), so diff `write_canonical_diff` failed on a dirty
  path until `docs/workflow` was added to `allowed_dirty_paths`.

## Proposed workflow changes (for human approval)

1. **`implement`: shrink the codex context and/or split slices.** Pass only the
   slice's tasks + the named target files, not the whole spec+plan; or cap slice
   size harder (~150 lines) and auto-split. Add a mid-run watchdog that aborts
   and reports at ~20 min of no commit instead of burning to 3600s.
2. **Fix the diff-checkpoint new-file hash mismatch** in the engine: either write
   the expanded patch to disk so the health hash matches, or hash the raw patch
   for the freshness check. (Documented in repo memory `ff-diff-checkpoint-gotchas`.)
3. **Expose a codex-review timeout** on `checkpoint` (like `--gemini-timeout-seconds`)
   and raise the default for spec/plan stages.
4. **`init` should seed `scope.json.allowed_dirty_paths` with `docs/workflow`**
   so the run's own telemetry never trips the diff dirty-path check.
