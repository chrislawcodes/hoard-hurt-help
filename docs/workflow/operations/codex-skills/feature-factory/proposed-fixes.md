# Feature Factory — Proposed Fixes Tracker

The rule: **any proposal appearing from 2+ different runs MUST become a tracked
fix** — it stops being a postmortem suggestion and becomes work someone owns.

How entries get here: `run_factory.py closeout` parses the run's
`postmortem.md` "Proposed workflow changes" section and appends any new
proposals below as `- [<slug>, <date>] <proposal one-liner>`. Exact-duplicate
lines are skipped. Manual edits are welcome — mark items done or in progress
inline; the closeout step never rewrites existing lines.

## Proposals

- [unified-connections, user-roles, strategy-first-onboarding] Codex review timeout too low — expose `--codex-timeout-seconds` on `checkpoint` and raise spec/plan defaults (observed need 300–540s). DONE: default is 540s end-to-end; regression test pins it (lane C 2026-07-02).
- [user-roles, dedup-engine-cseries] `[CHECKPOINT]` marker regex too strict — count markers only on their own slice line, not prose mentions. IN PROGRESS: lanes A/C 2026-07-02
- [strategy-first-onboarding, dedup-engine-cseries] deliver should push the branch first before `gh pr create` (stop and ask for a rebase when behind — never auto-rebase). IN PROGRESS: lanes A/C 2026-07-02
