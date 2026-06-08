# Hoard Hurt Help — Project Dashboard

> **How to use:** Review at the start of a session. The Feature Factory workflow
> updates this at closeout to record what shipped and what is now unblocked.

---

## Goals Overview

| Goal | Status | Notes |
|------|--------|-------|
| Feature Factory in this repo | 🟡 In progress | Engine ported from ValueRank; verifying end-to-end |

---

## Recently Shipped

- _Feature Factory engine ported into `docs/workflow/operations/codex-skills/` (this branch)._
- Connection setup now uses a draft/setup page and only creates the real connection on first authenticated contact; the connection detail page hides agent lists behind `Agent Details` and keeps `Rotate Key`/pause/delete controls at the bottom.
- Viewer mutual-help animation no longer draws the dashed connector line between paired agents.
- The `/me/agents/new` page now only shows the agent form when an active connection exists; otherwise it points users to `/me/connections`. Strategy presets are restored.

## Now Unblocked

- Running `/feature-spec` (and the full spec → plan → tasks → implement flow) drives the
  repo-owned runner at `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`.
- Connection onboarding can now safely wait for the provider's first live call before the real connection row exists.
- New agent creation uses the restored preset strategy picker, and users without a connection are sent to the connection setup page first.

## Notes

- Update this file at the end of each meaningful task. Mark work done and note what it unblocks.
