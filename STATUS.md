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

- Fail-loudly cleanup across 7 areas: unknown game types are rejected at match creation and cancelled (not zombied) by the scheduler; the poller escalates to CRITICAL after repeated subsystem failures; the migration guard logs every cancelled match; the OperationalError schema shims are replaced by a startup table check; connector LLM fallback moves are flagged `was_defaulted` end-to-end and the poll loop has a circuit breaker; lobby exception handling is narrowed to `SQLAlchemyError`; auto-matches cancel on bot seating failure; bot profiles are validated at seating time; MCP import failures are logged.
- Agent detail page regained the features lost in the Connection/Agent split (#225):
  a Matches section (watch / manage / leave), a "Ready to play → find a match" card,
  and contextual stall diagnostics with last-connected time on the status badge.
- _Feature Factory engine ported into `docs/workflow/operations/codex-skills/` (this branch)._
- Game admin dashboards now pass raw timestamps through to templates and render scheduled starts with the shared `localdt` filter, so a missing `scheduled_start` cannot crash `/games/<game>/admin/`.
- Connection delete now soft-deletes the connection so the runner receives an explicit shutdown response on its next poll, then exits; deleted connections are hidden from the normal UI and counts.
- Connection setup now uses a draft/setup page and only creates the real connection on first authenticated contact; the connection detail page hides agent lists behind `Agent Details` and keeps `Rotate Key`/pause/delete controls at the bottom.
- Viewer mutual-help animation no longer draws the dashed connector line between paired agents.
- The `/me/agents/new` page now only shows the agent form when an active connection exists; otherwise it points users to `/me/connections`. Strategy presets are restored.

## Now Unblocked

- Running `/feature-spec` (and the full spec → plan → tasks → implement flow) drives the
  repo-owned runner at `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`.
- The game admin dashboard can now survive a stale or broken match row instead of 500ing the whole page.
- Deleting a connection now acts as a real runner shutdown signal instead of a best-effort 401 on the next poll.
- Connection onboarding can now safely wait for the provider's first live call before the real connection row exists.
- New agent creation uses the restored preset strategy picker, and users without a connection are sent to the connection setup page first.

## Notes

- Update this file at the end of each meaningful task. Mark work done and note what it unblocks.
