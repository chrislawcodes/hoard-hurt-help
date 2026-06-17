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

- **Human player — play by hand** (branch `claude/human-player-game-join-9cr40t`, **not yet PR'd**) — a signed-in human can join a scheduled match with one click (no agent/connection/key) and play turns by hand in the viewer alongside agents and bots. A human is a `kind=human` agent (no provider, one frozen "human" version), reusing the existing Player/move-recording path. Built in 5 slices, all behind the full Preflight Gate (ruff + mypy + **1093 tests pass**, 31 new): **(0)** data model + migration `0038` (`Player.autopilot_at`) + `get_or_create_human_agent`; **(1)** `web_play.py` `POST …/play/{talk,act}` through the shared `record_player_action` (factored out of the bot service), with the bot auto-submit pass extended to **auto-Hoard a leaver immediately**; **(2)** no-setup join + leave (pre-start frees the seat, in-match → autopilot Hoard to the end, stays ranked); **(3)** the in-viewer play panel — Hoard pre-selected with payoff hints, type-ahead target picker, one-tap Pass, server-trusting countdown, near-deadline auto-submit, everyone-visible "waiting on N" pace indicator, phone-first CSS; **(4)** out-of-page alerts (browser notification + tab-title flash; permission requested at join; optional sound defaults off). Design resolved via an adversarial UX review (see `specs/016-human-player/`). Remaining: visual/design eyeball via the preview server, and a PR.
- **Strategy-first onboarding** ([PR #411](https://github.com/chrislawcodes/hoard-hurt-help/pull/411), merged `ae70137`) — agent creation is **decoupled from having a connection**: you design your agent (name + strategy + which AI) *before* connecting anything, it saves "ready — needs connecting", and you're then routed to connect *that* provider (a `?provider=` hint preselects the right client tab; the connect page + its 4s poll only short-circuit when the target provider is live). The Join hub now sends a no-agent user to **design first**, not connect first (reverses the connect-first routing from #372/#400). Agent list/detail gain an explicit needs-connecting state (status-aware: a *paused* connection still counts) + provider-scoped connect CTAs + batched queries (no per-agent N+1). No DB migration. Built via the **repo Feature Factory** (`docs/workflow/feature-runs/strategy-first-onboarding/`): spec (3 review rounds) → plan (4 rounds — caught the live-poll bounce, paused-status, all-provider mapping) → 4 diff-reviewed slices; 1024 tests pass. Manual real-client end-to-end is the remaining check.
- **Liar's Dice engine (game #2, Phase C foundation)** ([PR #371](https://github.com/chrislawcodes/hoard-hurt-help/pull/371), merged) — the pure rules engine (`app/games/liars_dice/engine.py`) + exhaustive unit tests, shipped via the **Direct arm of Experiment 10**. The **Feature Factory arm** built the *full* Phase C (engine + module + bots + viewer + admin) as [PR #377](https://github.com/chrislawcodes/hoard-hurt-help/pull/377) but is **intentionally kept open as a draft, not merged** — it's the comparison arm. **Verdict (see `experiments.md` #10):** FF's design reviews caught a real bot-seed determinism bug + ~15 integration gaps, but its implementation discipline silently collapsed (one giant slice → un-reviewable diff → a circular engine test passed CI). *Correction:* a later head-to-head ([PR #380](https://github.com/chrislawcodes/hoard-hurt-help/pull/380)) found the **Direct engine had a real `min_legal_raise` bug** the Factory engine didn't — and 6 of Direct's 692 test lines asserted the buggy values, so its bigger suite *hid* the bug. Lesson: test count ≠ correctness. #380 fixed the engine + added an independent minimality test. *Still open:* wiring the rest of Phase C (module/bots/viewer/admin) onto the merged engine — Factory's #377 is a reference, not mergeable as-is.
- **Smart gated Join flow** ([PR #372](https://github.com/chrislawcodes/hoard-hurt-help/pull/372), merged) — the join page is now a setup hub: one **Join** on a lobby game seats the operator's AI agent, and when setup is missing it redirects to the *existing* page for the first missing step (create-agent → connect/start your AI) carrying `?next=` back to the join URL, then forwards onward as each step completes (the connections live-status auto-advance now honors `?next`). No new page; reuse + glue. Adds a shared `safe_internal_next` open-redirect guard. 24 new tests. Shipped via the **Direct arm of Experiment 9** (Direct beat Feature Factory — see `experiments.md`; lesson: settled-design UI/flow → Direct, the planning ceremony re-derives a plan you already have). Not yet wired: the two backend bits we scoped separately — `get_next_turn` instant "no game" reply and the 10-minute idle auto-stop (see memory `mcp-play-flow-design`).
- **BYO Terminal: Mode A v1** (interactive MCP play) — players can point any MCP client at `/mcp` and watch their own AI play, nothing installed. `GET /api/agent/next-turn` gains an **opt-in bounded long-poll** (`hold_seconds`, default off → connector unaffected) so idle waiting stops burning model calls; per-connection `turns_played` + `api_call_count` counters (migration `0031`) show on the connection detail page; `setup-mcp.md` corrected (`X-Connection-Key`/`sk_conn_`) with a universal play-prompt + per-client connect snippets. Shipped via the **Direct arm of Experiment 8** (Direct beat Feature Factory — see `experiments.md`; lesson: route by silent-vs-test-visible risk, not backend-vs-UI). Follow-up filed: revalidate disabled-user / paused-connection mid-long-poll.
- **Practice Arena coach panel polish** — the sideline coach box now sits under the game title, uses a tighter layout, and opens a prompt window for course correction instead of the old chips.
- **Local migration unblocker** — Alembic migration `0023` now drops the historical winner FK on either `fk_games_winner_player_id_players` or `fk_matches_winner_player_id_players`, so the dev DB can upgrade cleanly again.
- **Reporting date filter** — the platform admin reporting page now accepts start and end dates, so admins can narrow turn-time distribution and slowest-match analysis to a completion-date window.
- **Sideline coach deploy fix** — migration `0030` now backfills `matches.coaching` with a dialect-safe boolean update, so PostgreSQL deploys no longer crash on `boolean = 0`.
- **Platform admin split** — the account-menu `Platform admin` entry now opens a hoverable submenu with `Match Admin` and `Reporting`, the match admin page lives at `/admin/matches`, the reporting page lives at `/admin/reports`, and the turn-time report summarizes completed match response times with per-match and bucket breakdowns.
- **Admin user management UI** — added `/admin/users` and `/admin/users/{user_id}` for platform admins, with search, pagination, role/status badges, connection and agent summaries, recent matches, audit history, and direct nav from the admin dashboard and handles page.
- **Disabled-account enforcement** — disabled users are now blocked from protected endpoints through both auth paths (web session and connection key), the app has a dedicated `/disabled` page, the nav reflects the disabled state, and Google login no longer demotes an existing in-app admin role on relogin.
- **Admin and regular user roles** ([PR #318](https://github.com/chrislawcodes/hoard-hurt-help/pull/318), open) — the platform now has two roles. Regular signed-in users can create matches from a slim flow (name + start time) and delete/cancel their own; admins can delete/cancel any match. Matches gain an owner (`created_by_user_id`); the admin role lives on `users.role`, seeded from `PLATFORM_ADMIN_EMAILS` at login (migration 0028 backfills existing admins). A per-user active-match cap (`USER_ACTIVE_MATCH_LIMIT`, default 3) bounds open match creation; admins are exempt. Creation, deletion, and cancel logic are consolidated into shared `app/engine/match_creation.py` + `match_deletion.py` helpers (the five old `max+1` id allocators and three cancel sites converged).
- **Baseline bot tournament** (#320) — added the `coin_flip` bot personality
  (random legal move, random table talk) as the control group, plus
  `scripts/baseline_tournament.py` (headless batches of 25 matches, 10 bots per
  table sampled with replacement from the 9 strategies, dedicated SQLite DB) and
  `scripts/export_baseline_dataset.py` (one CSV row per player-turn). Unblocks:
  generating the baseline dataset and training the win-probability model.
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

- The match viewer can start locally again on the current SQLite DB without a 0023 batch-migration crash.
- The Practice Arena coach box now has a tighter, title-adjacent layout and a prompt window for course correction.
- Platform admins can narrow the response-time report to a completion-date window when they want a shorter slice of production data.
- PR #334 can finish deploying cleanly after the sideline coach migration lands on production.
- Platform admins can jump from the top-right menu into match admin or reporting without hunting for a second screen.
- Platform admins can browse, inspect, and manage users from one dedicated admin screen instead of jumping between handles and incidents.
- Platform admins can disable an account without leaving a session or connection-key bypass.
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
