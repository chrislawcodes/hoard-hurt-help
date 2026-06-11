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

- The standings rail now shows `by @handle` for human agents again, while bots keep their platform credit.
- Bot names in standings and recent games now render without the internal match prefix; the UI uses the stable public bot name while keeping the per-match agent name unique behind the scenes.
- Sim table-talk phrases now sound more like natural player messages, reference
  the relevant player by seat name, and keep the same deterministic intent slots.
- **Lobby recent-games cleanup** — the lobby's recent-game rows now show the
  winning agent name instead of the seat string, and bot winners can be labeled
  clearly without leaking the human handle.
- **Standings rail cleanup** — the viewer's live standings now show agent names
  instead of `handle/agent` seat names, and bot rows are clearly labeled using
  the platform credit path (`@agentludum`).
- **Agent prompt cleanup** — strategy presets now contain only ranking guidance
  and strategy-specific behavior. The server generates the shared base prompt,
  rules, and 200-character response contract; turn payloads carry that canonical
  prompt for the connector, and agent setup/edit pages link to a readable preview.
- **Agent detail cleanup** — removed the stale "Playing" onboarding banner from the agent detail page, and the rename field now autosaves on change/blur instead of requiring a separate button.
- **Desktop wayfinding restored** — the shared nav now shows `Games` and `Leaderboard` inline on desktop again, while phones still collapse the same links into the hamburger/account menus. The marketing home's `How it works` override follows the same path.
- **Unified Connections** — a connection is now one machine running the connector,
  not one AI-provider login. Per-connection provider toggles (`connection_providers`),
  agents detached from connections and carrying a stored `provider`, and dynamic
  turn routing: each turn goes to any live connection covering the agent's provider,
  sticky per match with race-safe atomic-pin failover. Migration `0026` backfills
  existing data (no stalled games; unresolvable orphans archived, never aborts the
  deploy). Connector reports detected CLIs and trusts an explicit per-turn provider.
  555 tests green. The `/me/agents/new` form now uses a single grouped model
  dropdown, derives provider from the selected model, and disables providers that
  have no enabled machine coverage. Run docs:
  `docs/workflow/feature-runs/unified-connections/`. Pre-deploy: `--dry-run` the
  migration against a production-shaped DB; manual two-connector failover check.
- Unified the phone nav into a single right-corner menu: signed-in users get a ☰ + avatar pill whose panel carries wayfinding (Games / Leaderboard / page extras) above the account items; signed-out users get one ☰ menu with Sign in folded in. Desktop keeps inline links and the avatar menu unchanged.
- Fail-loudly follow-ups: missing Google OAuth config now fails startup on Railway (warns in dev); the admin add-bots form validates strategies before seating; and all loud-failure sites emit grep-able `ops_event=` structured log lines (new `app/ops_events.py`) covering match cancellations, poller failures/escalations, seating failures, bot profile rejections, replay/reconciliation fallbacks, and connector fallback moves.
- Fail-loudly cleanup across 7 areas: unknown game types are rejected at match creation and cancelled (not zombied) by the scheduler; the poller escalates to CRITICAL after repeated subsystem failures; the migration guard logs every cancelled match; the OperationalError schema shims are replaced by a startup table check; connector LLM fallback moves are flagged `was_defaulted` end-to-end and the poll loop has a circuit breaker; lobby exception handling is narrowed to `SQLAlchemyError`; auto-matches cancel on bot seating failure; bot profiles are validated at seating time; MCP import failures are logged.
- Agent detail page regained the features lost in the Connection/Agent split (#225):
  a Matches section (watch / manage / leave), a "Ready to play → find a match" card,
  and contextual stall diagnostics with last-connected time on the status badge.
- Onboarding status narration restored on the agent detail page: a live-polled card
  walks the user from "waiting to connect" → "connected — find a match" → "starts soon"
  → "waiting for its first move" → "playing — watch it play →".
- _Feature Factory engine ported into `docs/workflow/operations/codex-skills/` (this branch)._ 
- Game admin dashboards now pass raw timestamps through to templates and render scheduled starts with the shared `localdt` filter, so a missing `scheduled_start` cannot crash `/games/<game>/admin/`.
- Connection delete now soft-deletes the connection so the runner receives an explicit shutdown response on its next poll, then exits; deleted connections are hidden from the normal UI and counts.
- Connection setup now uses a draft/setup page and only creates the real connection on first authenticated contact; the connection detail page hides agent lists behind `Agent Details` and keeps `Rotate Key`/pause/delete controls at the bottom.
- Viewer mutual-help animation no longer draws the dashed connector line between paired agents.
- Match viewer replay now autoplays by default, so spectators do not need to hit Play before the turns advance.
- The `/me/agents/new` page now only shows the agent form when an active connection exists; otherwise it points users to `/me/connections`. Strategy presets are restored.

## Now Unblocked

- The standings rail can show the owner handle for human agents again without exposing the internal bot naming scheme.
- Bot standings and recent-game labels can stay clean even though bot agent names remain unique internally.
- Sim-only matches can be used for demos with less repetitive, scripted-sounding
  AI table talk.
- Recent lobby games can now read as agent names instead of human seat names.
- Spectator standings and replay rails can now read as agent names instead of
  human seat names, with bot rows clearly marked.
- Strategy authors can see the shared instructions before writing a strategy,
  without repeating game identity, rules, chat guidance, state format, or the
  response contract in every preset.
- The agent detail page no longer advertises a finished agent as still "Playing", and agent renames now save as soon as the user leaves the field.
- Running `/feature-spec` (and the full spec → plan → tasks → implement flow) drives the
  repo-owned runner at `docs/workflow/operations/codex-skills/feature-factory/scripts/run_factory.py`.
- The game admin dashboard can now survive a stale or broken match row instead of 500ing the whole page.
- Deleting a connection now acts as a real runner shutdown signal instead of a best-effort 401 on the next poll.
- Connection onboarding can now safely wait for the provider's first live call before the real connection row exists.
- New agent creation now uses the grouped model picker with provider-aware availability notes, and users without a machine are pointed at the connection setup page first.
- Spectators watching a match can now let the replay run automatically instead of manually starting playback.

## Notes

- Update this file at the end of each meaningful task. Mark work done and note what it unblocks.
