# Feature Factory run — 8/4 betrayal payoff change (experiment arm)

This is the **Feature Factory arm** of the thin-vs-factory delivery experiment.
Feature: re-split the betray-a-helper payoff so the attacker **rises +4** instead
of the victim **cratering −8**. Net swing (12 pts) is unchanged; today attacker
+4 / victim −8 becomes attacker +8 / victim −4.

Engine: `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`,
driven Claude-only (`FF_REVIEWER=claude`) as the Claude orchestrator.

Worktree: `/tmp/wt-betrayal-8-4-factory` · branch `exp-factory/betrayal-8-4` ·
own venv at `.venv`. Base SHA at start: `6799bb0123823cc75bde3ce9fd06255ea931dcb9`.

---

## 1. Stage table

Columns: Stage | Artifact | started_at | finished_at | sha_before | sha_after |
review_rounds | findings_raised | findings_accepted | artifact_revised
(`artifact_revised` = did a review finding change the artifact, i.e. sha_before != sha_after).

| Stage | Artifact | started_at | finished_at | sha_before | sha_after | review_rounds | findings_raised | findings_accepted | artifact_revised |
|---|---|---|---|---|---|---|---|---|---|
| init | state.json | 2026-07-07T06:24Z | 2026-07-07T06:25Z | 6799bb01 | 6799bb01 | n/a | n/a | n/a | n/a |
| discover | state.json | 2026-07-07T06:25Z | 2026-07-07T06:26Z | 6799bb01 | 6799bb01 | n/a | n/a | n/a | n/a — engine routed to FULL FEATURE FACTORY |
| spec | spec.md | 2026-07-07T06:26Z | | 6799bb01 | | | | | |
| design (reuse+docs) | reuse-report.md + docs | | | | | | | | |
| plan | plan.md | | | | | | | | |
| tasks | tasks.md | | | | | | | | |
| implement (slices) | code+tests+docs | | | | | | | | |

---

## 2. Friction log

One bullet per engine breakage / workaround / babysitting event. First-class metric — log EVERYTHING.

- (none yet)

---

## 3. Did review findings change artifacts?

Tracked per stage in the table via `artifact_revised`. Running count of
artifact-changing findings will be summarized here at the end.

- (pending)
