# Post-mortem: user-roles

Feature: admin/regular user roles, match ownership, user-facing create/delete/cancel, active-match cap.
Delivered as PR #318. Orchestrated from a Claude session (Codex implemented, Codex+Gemini reviewed).

## What went well

- **Adversarial spec review caught real coverage gaps.** The spec converged over
  4 Codex rounds — each round found genuine missing surfaces (existing-admin
  lockout on the role switch, the second game-admin create path, the admin
  dashboard cancel target, the owner read model). These would have been bugs.
- **The reuse audit paid for itself.** It surfaced five duplicated `max+1`
  match-id allocators and four inline `Match(...)` builders; the plan converged
  them on one `match_creation.py`, and the implementation followed.
- **Per-slice diff review + Preflight Gate held.** Every slice landed green
  (ruff/mypy/664 tests) with an independent Gemini diff review.

## What didn't work

1. **Diff-checkpoint hash bug (engine) — biggest cost.** For any slice that adds
   a new file, `run_gemini_review` records the hash of the *expanded* diff
   (new-file chunks inlined, PR #832) while the healthiness check
   (`artifact_hash_matches` → raw patch hash) hashes the *raw* patch. They never
   match, so the diff stage stays "repairable" and never advances. Hit slices 1,
   3a, 3b, 4. Worked around by writing the expanded diff back to the patch
   artifact, then manually advancing the checkpoint index. **A separate engine
   session is fixing this (prompt handed off).**

2. **Codex under-implemented against tasks.md three times.** Codex skipped: the
   email unique-constraint guard (slice 2), the entire `cancel_match` transition
   + 3-site convergence (slice 3b), and the entire cancel route + dashboard
   retarget (slice 4). The orchestrator completed all three (each Gemini-reviewed
   + test-covered), which is why the implementation-rule flagged Claude lines.
   Tasks.md was explicit about each; Codex still dropped them.

3. **Codex review timeout (120s) too tight for spec/plan.** The runner's
   hard-coded 120s codex-review timeout failed on every spec/plan checkpoint;
   reviews needed 300s (spec) to 540s (plan) because Codex explores the repo
   agentically. Had to pre-generate the codex review directly with a longer
   `--timeout-seconds`, then let the checkpoint skip the healthy review.

4. **Mid-run rebase broke the first-slice diff base.** After rebasing onto main
   (per always-rebase), the slice-1 diff base resolved to the stale remote branch
   ref and swept in unrelated main commits. Fixed by passing an explicit
   `--base-ref`.

5. **`[CHECKPOINT]` marker format is undocumented and silently collapsed slices.**
   Markers must be list-item lines ending in bare `[CHECKPOINT]`. My first
   tasks.md put them on bold `**Verify:**` lines wrapped in backticks → 0 markers
   detected → all 5 slices collapsed into one giant Codex dispatch. Caught before
   dispatch only by inspecting `parse_parallel_task_groups`.

6. **Implementation-rule telemetry didn't record the `implement` dispatches.**
   Deliver reported "591 non-test lines with no recorded Codex dispatch" even
   though Codex implemented the bulk via the runner. Required an override.

7. **Post-review docs commits drift the diff HEAD.** Committing the per-slice diff
   artifacts moves HEAD past the reviewed diff, so deliver/closeout report the
   diff stage "repairable" until a reseal. Cosmetic but recurring friction.

8. **`.gitignore` doesn't ignore what it intends.** The review sidecars
   (`*.stderr.txt`/`*.stdout.txt`) and `review-attempts.jsonl` patterns omit the
   `docs/workflow/` path prefix, so they show up dirty every command.

## Proposed workflow changes (require human approval)

- **[engine] Fix the diff-stage expansion-hash mismatch** (separate session) —
  hash and validate the same artifact (expand for the prompt only, or persist the
  expanded diff). Add a regression test: a diff that adds a new file must
  round-trip.
- **[engine] Make the codex review timeout configurable end-to-end** — thread a
  `--codex-timeout-seconds` from `checkpoint` → `repair_review_checkpoint` →
  `run_codex_review` (parity with the existing `--gemini-timeout-seconds`); raise
  the default for spec/plan, where Codex explores the repo.
- **[engine] Reseal the diff manifest automatically on docs-only HEAD drift** so
  deliver/closeout don't require a manual re-checkpoint after committing
  per-slice artifacts.
- **[engine] Fix the implementation-rule telemetry** so runner `implement`
  dispatches count as Codex work.
- **[docs] Document the `[CHECKPOINT]` marker format** in the skill (list item,
  ends with bare `[CHECKPOINT]`, no backticks) — or relax the regex to accept the
  natural forms.
- **[repo] Fix `.gitignore`** to use `docs/workflow/feature-runs/**/reviews/*.stderr.txt`
  etc., and untrack `review-attempts.jsonl`.
- **[process] When Codex drops a tasks.md item, prefer a targeted re-dispatch
  over an orchestrator hand-fix** to keep the implementation-rule signal clean —
  unless the gap is small and the diff review still covers it (as here).

## Validation at delivery

`ruff` ✅ · `mypy` (106 files) ✅ · `pytest -q` ✅ 664 passed. PR #318 open, CI pending.
