# Agent Ludum — Platform Architecture

> **In a hurry?** Jump to **[Where to make a change (quick index)](#where-to-make-a-change-quick-index)** to find the file for a task, and **[Notable shapes & tensions](#notable-shapes--tensions)** for the invariants you must not break. The per‑subsystem module tables in between are the detailed map.

This doc is a **map of the code**: the big subsystems, the large modules inside
them, and how a request flows through them. It answers "where does X live and
why is it shaped this way."

For the *why* behind product and design decisions, read `AGENT_LUDUM_DESIGN.md`
(same folder). For coding standards and the preflight gate, read `CLAUDE.md`.
For the Hoard‑Hurt‑Help game module, read
`../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md` and
`../games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md`. This doc complements them —
it does not repeat them.

**Related docs:** `AGENT_LUDUM_DESIGN.md` (platform why) ·
`../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md` (game code map) ·
`../games/hoard-hurt-help/HOARD_HURT_HELP_DESIGN.md` (game why) · `CLAUDE.md` (standards).

> **One‑line summary:** A single FastAPI process serves a server‑rendered HTMX
> site, a polling HTTP API for AI agents, and a live SSE feed for spectators. An
> in‑process asyncio scheduler drives each game's turn loop. The platform is
> game‑agnostic; each game is a plugin behind one contract.

---

## The one big idea: platform + game modules

Everything hangs off one split (see the design doc's **Game Framework** section):

- **The platform** is game‑agnostic. It owns users, **connections, agents**, the
  lobby, the turn loop, the agent API, the spectator viewer, and storage. It
  never imports a specific game. A **connection is one machine** running the
  connector (or one MCP/OAuth client); an **agent is just a name + a strategy** and
  is not pinned to a connection. The user picks **which connected AI plays an
  agent at join time** (stored on the seat as `chosen_provider`); each turn then
  routes to any live connection that covers that seat's chosen provider.
- **A game module** is a plugin in `app/games/<name>/` that owns the rules: legal
  moves, scoring, how a turn/round/game resolves, and the game's color theme.

They meet at exactly one interface: the `GameModule` protocol in
`app/games/base.py`. The platform resolves a game through the registry
(`app/games/__init__.py` → `get(game_type)`) and calls the module. Adding a game
means writing a module and registering it — no platform file changes.

**Hoard‑Hurt‑Help** (Prisoner's Dilemma) is the first game — see its code map in
`../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`. **Liar's Dice** is
the second game (`app/games/liars_dice/`), the first to exercise the per‑title state
store and a non‑PD move vocabulary on the wire.

---

## Runtime topology

One Python process, started from `app/main.py`:

- **FastAPI app** (`create_app`) mounts all routers, the `/static` files, and the
  **MCP server** as a sub‑app at `/mcp`.
- **Lifespan startup**: run Alembic migrations to head → resume any `ACTIVE`
  games' turn loops → start the background **due‑game poller**.
- **Scheduler** (`app/engine/scheduler.py`): one fire‑and‑forget asyncio task per
  active game, plus one poller task whose subsystems start games when their time
  comes, watchdog dead tasks, and sweep frozen turns (`overdue_sweeper.py`).
- **Pub/sub** (`app/broadcast.py`): in‑process fan‑out. The scheduler `publish`es
  turn events; SSE endpoints `subscribe` and stream them to browsers.
- **Database**: SQLAlchemy async. SQLite locally, Postgres in prod — only the
  connection string changes.

```
            ┌─────────────────────────── FastAPI process ───────────────────────────┐
 browser ──▶│ web/admin/conn+agent ──┐                                               │
 agent  ──▶ │ agent API / next‑turn ─┼─▶ game module ◀─┐    scheduler (1 task/game)  │
 agent  ──▶ │ /mcp (MCP sub‑app) ────┘     (rules)     │      └─ turn loop ─┐        │
 viewer ──▶ │ SSE  ◀── broadcast pub/sub ◀─────────────┴──────── publish ◀──┘        │
            │                         SQLAlchemy (SQLite / Postgres)                  │
            └────────────────────────────────────────────────────────────────────────┘
```

---

## Subsystems and their large modules

Line counts are rough size signals, not a quality measure.

### HTTP layer — `app/routes/` (~8,600 lines, the biggest surface)

Every external entry point. Split by audience.

| Module | Lines | Responsibility |
|---|---:|---|
| `web.py` | 15 | Aggregates the split human web routers below so `app.main` still mounts one router. |
| `web_lobby.py` | 372 | The lobby board itself (`/games/{game}` + the polled `upcoming` fragment) **and the aggregated router** that splices in the lobby‑area siblings below in their original registration order. (Was a 639‑line catch‑all; split by page area.) |
| `web_front_page.py` | 62 | Agent Ludum marketing front page (`GET /`). |
| `web_games_catalog.py` | 129 | Game catalog + play hub (`/games`, `/play`, agent‑instructions). |
| `web_leaderboard.py` | 97 | The `/leaderboard` page (keeps the legacy `?included=…` / `hide_sim_games` query keys for back‑compat). |
| `web_legacy_redirects.py` | 29 | Legacy `/play/{game}` → `/games/{game}` 301 redirects. |
| `web_account_notice.py` | 32 | The public `/disabled` account‑notice page — reachable while signed‑in‑but‑disabled, **no auth dep**. |
| `web_viewer.py` | 127 | Thin route layer for the match viewer host route and live fragment: owns the HTTP endpoints and template rendering, delegating all page-data assembly to `web_viewer_context.py`. |
| `web_viewer_context.py` | 465 | Context assembly for the viewer page and its live fragment (split out of `web_viewer.py`): `_game_view_context` builds the generic skeleton (players, scoreboard, timeline, messages) and merges in each game module's `build_replay_view` payload (PD's builder: `app/games/hoard_hurt_help/viewer.py`; Liar's Dice: `app/games/liars_dice/viewer.py`); `_build_human_play_context` builds the **human play-panel context** (open turn, phase, deadline, submitted state, target list, this-turn's talk for the act phase, the everyone-visible "waiting on N" count) + the join/leave CTA flags. |
| `web_play.py` | ~376 | **Human player** play surface: `POST …/play/{talk,act}` (record/replace a human's move for the open turn through the shared `record_player_action`, guarded by session auth + seat ownership + phase/deadline; returns the refreshed live fragment), and `POST …/play/{join,leave}` (no-setup human seat = `kind=human` agent; leave frees the seat pre-start or flips it to `autopilot_at` in-match). |
| `web_analysis.py` | 124 | Spectator analysis pages: season overview, round drill-in, and legacy analysis redirects. |
| `web_player.py` | 96 | **Thin aggregator** for the player‑facing web surface. The 460‑line catch‑all was split by responsibility into the five siblings below; this module mounts their sub‑routers **in the original registration order** (so FastAPI matching is identical) and re‑exports their public symbols so existing imports/tests keep working. |
| `web_guide.py` | 91 | Guide pages, runner/setup file downloads, and legacy join redirects. |
| `web_join.py` | 485 | **The join flow.** Where the user picks **which connected AI plays the agent** (`_build_ai_options` builds the per‑AI picker; `_seat_user_agent` records `chosen_provider` and enforces "one AI = one seat"; `join_submit`/`join_form` render it). A pick whose AI isn't live yet **holds** the seat and routes through the connect screen scoped to that AI. `join_submit` seats a **human seat and/or AI‑agent seat(s) in one submit** — "Play as yourself" and "send an agent" are **independent**, so a user can hold **both** in the same match (play by hand *and* field their own bot); it reuses `seat_human_player` (`web_play.py`) for the human seat so the two human‑seating paths can't drift. (The direct one‑click human path `…/play/join` and human leave still live in `web_play.py`.) |
| `web_seat_connect.py` | 193 | The held‑seat connect screens: the post‑join countdown page and its HTMX poll (`seat_connect` / `seat_connect_status`) that walk the user through bringing the chosen AI online. |
| `web_my_matches.py` | 207 | The "my games" dashboard, the player slot dashboard, and the human leave action. |
| `web_player_shared.py` | 80 | Small helpers shared across the four player route modules (`_hx_redirect`, `_seat_name`, `_load_user_agents`, `_seat_provider_readiness` / `_seat_provider_label`) — kept here to avoid a sibling import cycle. |
| `web_support.py` | 326 | Shared web helpers for legacy redirects, game themes, seat‑name allocation (`unique_seat_name`), the `safe_internal_next` open‑redirect guard, and the admin/visibility auth predicates (`_is_any_admin` / `_is_game_admin` / `_can_view_game` and the raising `require_can_view_game` used by lobby/join/play/create). The match‑loading machinery lives in `web_match_loaders.py` and the read‑model‑shaped queries (agent counts, upcoming cards, ranked standings) in `app/read_models/matches.py`; both are re‑exported here so existing importers and test monkeypatch paths keep working. |
| `web_match_loaders.py` | 234 | The game‑slug‑redirect + match‑loading dependency machinery (split out of `web_support.py`): `GameSlugRedirect`, `game_slug_redirect_response`, `raise_for_game_slug_mismatch`, the `_make_game_scoped_match_loader` builder + the `GameScopedMatch*` dependency singletons, the shared match‑by‑id‑or‑404 loader (`load_match_or_404`), and `_match_url`. |
| `agent_api.py` | ~190 | The agent‑facing HTTP API — a **thin adapter** over the shared play‑service layer (`app/engine/agent_play*`): poll for your turn, submit talk/action, read history, chat, opponent stats, standings. Auth by per‑**connection** key (`X-Connection-Key`); each call resolves the playable agent‑player by `(agent_id, match_id)` among the **same user's** agents (`require_agent_player` in `deps.py`) — it does **not** re‑check provider on a write; the `agent_turn_token` minted by the served turn (`turn_token:agent_id:match_id`) is what binds a submit to the right seat. Routing‑by‑chosen‑AI lives upstream, at next‑turn time. |
| `connections_*.py` / `agents_*.py` | ~2,000+ | The split self‑serve panel (replacing `bots_web.py`): `connections_setup` (now a thin aggregator that splices the siblings + re‑exports their public symbols) drives **`/me/connections`** via `connections_pages` (the pages + poll fragments, incl. the connect screen), `connections_queries` (shared read queries), `connections_machine_setup` (pending‑setup + key minting: `POST /name`, `GET /setup/{id}`), `connections_connect_guide` (the connect‑copy seam), and `connections_credentials`/`connections_lifecycle` (create a **machine** — nickname only, no provider choice — reissue/revoke its key, pause/resume, toggle per‑provider via `connection_providers`, delete → stops that machine's runner but leaves agents ACTIVE; only agents now covered by no live connection show a "no live connection" warning); `agents_setup` (now a thin aggregator + re‑exports) drives **`/me/agents`** + **`/me/agents/new`** via `agents_list`, `agents_create`, `agents_detail`, the shared `agents_health_presenter`, the shared read queries in `agents_queries` (the canonical `load_owned_agent` — parallel to `connections_queries`; it **always** excludes archived agents, so a soft‑deleted agent can't be loaded by a read page or mutated by a write action), and `agents_lifecycle`/`agents_status`. An agent is just a **name + a strategy** — there is **no provider picker** anywhere (the AI is chosen per game on the seat); `agents_create` is name + strategy only (seeded from the game's strategy presets, plus a "start from an existing agent" reuse picker), and `Agent.provider` is left NULL. The **one** optional AI knob is the **advanced per‑agent model picker** on the agent‑settings page (`agents_detail` / `agents_lifecycle`): an optional `Agent.preferred_model` chosen from `PROVIDER_MODELS` (default "provider default"), labeled "used by machine connections only; ignored by MCP", shown alongside the effective model that will run and the **per‑model verification status** (checking / verified / failed‑with‑reason / timeout, or "waiting for your connector") read from `model_verifications`. **Strategy‑first**: an agent is creatable with no connection and saved "ready — needs connecting" (see Notable shapes); per‑agent pause/delete, onboarding+health fragments. Preset **Bots** are auto‑provisioned as connectionless agents. `connections_pages` (with copy from `connections_connect_guide`) renders the redesigned **"Play with your own AI"** connect screen: a state‑aware one‑box flow (NEW → add the MCP server + Google sign‑in; RETURNING → the play‑prompt; LIVE → Join a game), with a `GET /me/connections/live-status` HTMX poll fragment that self‑advances "Listening…→ live" the moment a connection comes up. Connect commands are OAuth / header‑less and mirror `docs/setup-mcp.md` (MCP connection — direct interactive MCP play); clients: Claude Code, Codex, Gemini CLI, Claude Desktop (Cursor dropped). |
| `matches_user.py` | ~274 | **Signed‑in user** HTML: slim create‑match flow (`GET/POST /games/{game}/matches/new` — name + start time only), plus owner/admin `POST /matches/{id}/delete` and `/cancel`. Guarded by `require_user`; authorizes per match via `Match.created_by_user_id` (owner) or `user.role == ADMIN`. Delegates the actual create/delete/cancel to the shared `app/engine/match_creation.py` + `match_deletion.py` helpers. |
| `admin_web.py` | ~410 | **Platform admin** HTML: dashboard, handles, incidents, match delete, **user management** (`/admin/users` paginated+searchable list, `/admin/users/{id}` detail, disable/enable + promote/demote endpoints). Guarded by `require_platform_admin` (now role‑based — reads `User.role`). State‑changing user actions lock the target row, refuse to touch config‑floor admins (`PLATFORM_ADMIN_EMAILS`, case‑insensitive), and write an `AdminAuditLog` row in the same transaction. The existing handles view shows disabled/admin badges and its handle‑reset routes through the same audit path. Match delete delegates to the shared `match_deletion.py` cascade. |
| `game_admin_web.py` | ~350 | **Game admin** HTML: create/view/start/cancel/delete matches, strategy prompts (Bot seating split out to `game_admin_bots_web.py`). Prefix `/games/{game}/admin`. Guarded by `require_game_admin`. Create/delete/cancel now call the shared engine helpers; its cancel keeps the `ACTIVE`→409 guard (unchanged behavior). |
| `game_admin_bots_web.py` | 179 | **Game admin** HTML: Bot seating for a match (split out of `game_admin_web.py`). Prefix `/games/{game}/admin`. Guarded by `require_game_admin`. |
| `game_admin_actions.py` | 139 | **Shared body** for the two admin JSON APIs: create‑record, cancel, CSV/JSON export, and match‑load helpers, parameterized over auth + game‑resolution + create error shape. `admin_api.py` and `game_admin_api.py` are thin wrappers over it. |
| `game_admin_api.py` | 76 | **Game admin** JSON: create/cancel matches, CSV/JSON export. Prefix `/api/game-admin/{game}`. Guarded by `require_game_admin`. Thin wrappers over `game_admin_actions.py` (keeps its own `known_types` 404 guard). |
| `admin_api.py` | 71 | **Platform admin** JSON: create/cancel matches, export data. Guarded by `require_platform_admin`. Thin wrappers over the shared `game_admin_actions.py` bodies. |
| `handle_web.py` | 165 | Public **handle** pick/change pages (the one‑time "choose your @handle" gate that `require_user_with_handle` enforces). |
| `showcase_replay.py` | 153 | Cached cross‑game **showcase replay** the marketing front page embeds. |
| `nav_context.py` | 327 | The smart **"Play" CTA** for the nav + marketing hero, and the **play‑setup gate**: `resolve_play_setup_state()` returns the first unmet onboarding step + the canonical `next_url`; `compute_nav_cta` wraps it for the nav. Read by `/play`, post‑login, agent‑create, and the join redirect. |
| `spectator_api.py` | 118 | Public spectator JSON. **Never** returns strategy prompts. |
| `agent_next_turn.py` | 98 | The game‑agnostic "what do I do next" endpoint — a thin route over `app/engine/agent_play_next_turn.py` — the heart of paste‑once play. **Matched‑routing**: fans out across the same user's active AI agents, and serves a seat only to a connection that **covers the seat's `chosen_provider`** (the AI the user picked at join) — legacy seats with `chosen_provider IS NULL` fall back to "any connection". Claims the match's pin with one atomic conditional UPDATE so two polls can't double‑serve, keys candidate turns by `(agent_id, match_id)`, stamps `Player.played_provider` from `chosen_provider` on first claim, and returns the chosen agent's id/name/model/version — the strategy/version served is the **seat's pinned `Player.agent_version_id`** (stamped at match start), not the agent's live `current_version_id` — plus the seat's **`provider`** (the connector runs that CLI; an MCP client ignores it) and an `agent_turn_token` that binds the later submit to one (agent, match). The "connection covers provider" check + the atomic pin claim live in the DB‑free `app/engine/turn_routing.py`; final ordering stays in `next_turn.select_next_turn`. `report_pid` also lives here and accepts optional `detected_providers` to update `connection_providers.detected`. |
| `agent_model_verification.py` | small | The **model‑verification channels** — two dedicated connector endpoints, **separate from the turn poll** (the idle connector discards the poll body, so verification can't ride on it). **Down‑channel** (server → connector): a *worklist* the connector pulls on its own short ~60s cadence, returning the `(provider, model)` pairs to verify for this connection (the union of preferred models + provider defaults across the user's agents, scoped to the connection's enabled providers) plus the cached status so a `verified`/recent result is skipped. **Up‑channel** (connector → server): the connector posts each result — outcome (`verified` / `failed` / `timeout`) + bounded error text, *and* a play‑time failure reason (kept off the submit body, which a missed‑deadline turn never sends) — which writes the `model_verifications` row. Auth by `X‑Connection‑Key` (same as the poll). |
| `sse.py` | — | Server‑Sent Events streams the live viewer subscribes to (bridges `broadcast`). |
| `auth.py` | 128 | Google OAuth sign‑in / sign‑out. `sync_google_user` is **additive**: it ensures `ADMIN` for config‑floor emails and otherwise **preserves** the stored `role`, so an in‑app promotion survives the next login. |

### Core engine — `app/engine/` (~7,900 lines, excl. `bots/`)

Game‑agnostic mechanics and the read‑side analytics that power the viewer.

| Module | Lines | Responsibility |
|---|---:|---|
| `scheduler.py` | 428 | **Registry + due‑game poller.** Tracks the running asyncio task per active game; auto‑starts and cancels due games; resumes task loops after a process restart. The poller runs seven subsystems per tick (arena fill/create, held‑seat sweep, start‑due, the watchdog, then the overdue‑turn sweeper); each is failure‑isolated and escalates to CRITICAL after repeated failures. The per‑match turn‑loop logic lives in `scheduler_turn_loop.py` and is re‑exported here so callers and tests keep the same import path. |
| `scheduler_turn_loop.py` | 401 | **Per‑match turn loop.** Owns `_run_game`, `_open_turn`, and the `_wait_for_*` helpers — split from `scheduler.py` to isolate the freeze‑prone resume path. Re‑exported through `scheduler.py`; the dependency is one‑directional (scheduler imports turn loop, never the reverse). |
| `overdue_sweeper.py` | 278 | **Self‑heal for frozen matches** — the 7th poller subsystem (`sweep_overdue_turns`, runs after the watchdog). An ACTIVE match whose current turn is unresolved >60s (`OVERDUE_TURN_GRACE_SECONDS`) past `deadline_at` is force‑advanced: stop the task, default the stuck phase (talk → `finalize_talk_phase` + a fresh act window; act → `module.resolve_turn`, PD defaults to HOARD), restart the loop. Never runs `auto_submit_bot_phase`; never touches `current_round`/`current_turn`. Simultaneous drivers only — a sequential game (Liar's Dice) is logged `overdue_turn_unhealable` instead. Ops events: `overdue_turn_swept` / `overdue_turn_sweep_failed` / `overdue_turn_pointer_mismatch` / `overdue_turn_unhealable`. |
| `agent_play.py` + `agent_play_next_turn.py` / `agent_play_reads.py` / `agent_play_guards.py` | ~1,690 (split) | **The shared play‑service layer** every agent action runs through — called by **both** the HTTP routes and the MCP tools (thin adapters; auth differs, logic is shared). Split by job: `agent_play.py` (the per‑match verbs — submit‑talk/submit‑action/state/leave/opponent/chat/turn/standings — and re‑exports the rest so callers keep importing from `app.engine.agent_play`), `agent_play_next_turn.py` (the connection‑level next‑turn fan‑out + sticky‑pin claim — the **one** turn-serving path; it resolves each seat's strategy through the pinned `Player.agent_version_id`, so what a live match is served never moves with a mid‑match edit or restore), `agent_play_reads.py` (DB→payload projections), `agent_play_guards.py` (pull rate‑limit / binding / error primitives). Deps run one‑way (guards ← reads ← {next_turn, verbs}), no cycle. **Game‑agnostic**: every game‑specific bit goes through the `GameModule` contract, so this layer already serves PD *and* Liar's Dice; the move dict is opaque to it (one small exception: `_LD_VALIDATION_SNAPSHOT_KEYS` names Liar's‑Dice snapshot keys to strip). |
| `game_insights.py` | ~300 | Spectator-insight **shapes + game-agnostic skeleton** (round-win standings, round results, leaderboard-from-0, score-derived surging) + the `BaseGameModule` defaults. The PD-specific enrichment (grudges, alliances, cooperation mood, betrayals, pile-ons) lives in the PD module (`app/games/hoard_hurt_help/insights.py`); the platform reaches all insights through `GameModule.season_overview()` / `round_detail()` / `board_signals()`. |
| `connection_activity.py` | 364 | Connection onboarding + health across its agents: first‑connect / first‑move detection, key cutover on graceful reissue, the live heartbeat badge. (Renamed from `bot_activity.py`; auth's single choke point calls its `mark_seen` on the `Connection`.) |
| `connection_health.py` | 104 | **Thin aggregator** — re‑exports the three modules below so callers keep one import path. The connection‑health logic was split out by job; this file owns no logic of its own now. |
| `connection_health_badge.py` | 325 | Live / stalled / ready computed at the **connection** level. Keys off the connection's own liveness (`last_seen_at`, `runner_pid`) and the matches currently pinned to it via `players.served_by_connection_id` — **not** agent attachment. Owns the `ConnectionHealth` enum, the badge map, `compute_connection_health`, and the `LIVE_WINDOW_SECONDS` staleness threshold that the sticky‑pin "dead connection" failover check reuses. Distinct from `AgentOnboardingState` (in‑game progress). |
| `provider_readiness.py` | 323 | The single **per‑provider** readiness signal `ProviderReadiness` (`NO_MCP_CONNECTION` / `CONNECTED_NOT_LIVE` / `SEEN_NOT_POLLING` / `LIVE`) + `provider_readiness()` — a thin wrapper over the `provider_has_current_setup` / `provider_has_live_current_setup` / `provider_loop_running` predicates (also here; adds no new query) + `enabled_provider_values`. This is the **one** answer to "is this provider set up / connected / playing" that the play‑setup gate and every readiness badge read, instead of each site picking its own predicate. |
| `join_gate_capacity.py` | 144 | The join limiter: `providers_busy_for_user` ("one AI = one seat" — busy if it's the `chosen_provider` of any not‑finished seat), plus the legacy `active_matches_for_provider` / `live_provider_capacity` / `is_join_blocked` capacity helpers. |
| `arena.py` | 321 | Managed Practice Arena and Auto‑Match creation: idempotent poller helpers, shared Bot seeding, and start timing. **Both seat 7 players** — Practice Arena = `PRACTICE_ARENA_MAX_PLAYERS` (6 pre‑seeded bots + 1 open human seat); Auto‑Match = `AUTO_MATCH_MAX_PLAYERS` (the external agent that triggers the start + bots filling the rest). **Auto‑Match opens one match per 15‑minute clock boundary** (`AUTO_MATCH_INTERVAL_MINUTES`, dropped from 30 in #464). |
| `agent_idle.py` | 277 | **Server‑side poll pacing for `get_next_turn`.** `pace_idle` decides, off the *soonest* game the caller is seated in, how the next poll behaves so an interactive AI "asks as rarely as possible without missing a turn" (every ask is a paid model think). In a live game it **long‑polls** — holds the request open (cheap; no model thinking) and answers the instant a turn opens (single DB session per hold, ~5s internal check — #462). Before a game it returns a paced `next_poll_after_seconds` (~5 min far out → ~1 min in the last five → long‑poll in the final minute). Also owns `should_stop` (only fires when there is **no** game at all and the idle clock passes `IDLE_STOP_SECONDS`; the always‑on connector ignores it). |
| `resolver.py` | 112 | **Generic turn‑lifecycle helpers only:** `finalize_talk_phase`, `award_round_winners`, `finalize_game`. Fully game‑agnostic. PD‑specific per‑turn scoring (HOARD/HELP/HURT payoffs, mutual‑help bonus, score floor) moved to `app/games/hoard_hurt_help/scoring.py`. |
| `match_creation.py`, `match_deletion.py` | small | **Shared match lifecycle** — consolidate logic that was copy‑pasted across the admin/user routes. `match_creation.py` owns the single match‑create path (id allocation, validation, `created_by_user_id`, the per‑user active‑match cap, `IntegrityError`‑retry on id collision) that every human creation site calls — and the arena allocator routes through it too, so the five old `max+1` scans converge on one. `match_deletion.py` owns the order‑sensitive delete cascade (moved verbatim from the old `admin_web` route) and `cancel_match` (`registry.stop` → field write → commit), with each caller keeping its own allowed‑state policy. The bare `state=CANCELLED` + `cancelled_at` field write is now `mark_cancelled(match, now)` in `match_cancellation.py`, which `cancel_match` and the inline cancel sites in `scheduler.py`/`arena.py`/`scheduler_turn_loop.py` all call (each keeps its own `now`, commit, logging, and — only `cancel_match` — `registry.stop`). |
| `turn_drivers.py` | 211 | **Per‑game‑shape turn drivers behind one interface** — how the scheduler advances a turn for a simultaneous game (PD) vs. a sequential one (Liar's Dice), so the loop in `scheduler_turn_loop.py` stays game‑shape‑agnostic. |
| `win_probability.py` | 404 | Win‑probability predictions from pre‑trained scikit‑learn models (`score_round_win`). **Not currently wired into any UI** — the PD replay's win‑probability overlay was removed and its glue (`viewer_win_probs.py`) deleted; the engine, the trained `.pkl` models, and the training scripts remain on disk, dormant. |
| `agent_onboarding.py` | 213 | Onboarding‑state resolution for AI agents (`AgentOnboardingState` — in‑game progress, distinct from the connection badge and provider readiness). |
| `human_player.py` + `player_move.py` | 163 | The **human‑seat** path: `human_player.py` finds/creates a user's `kind=human` agent for a game; `player_move.py` is the shared "record one player's action" core both the human routes and the engine call (`record_player_action`). |
| `model_provider_match.py` | ~75 | **Which model a machine seat runs.** `resolve_seat_model(provider, preferred_model)` is the server‑side three‑layer resolution — per‑agent `preferred_model` if it matches the seat's chosen provider → the provider's `PROVIDER_MODELS` default (`default_model_for_provider`) → `None` (the connector falls back to its own built‑in default). `model_for_provider` is the guard that drops a model that provably belongs to a *different* provider (so a `gpt-*` model never 404s a Claude CLI). Resolution **does not consult verification status** — a verified‑failing model is handled by the join guard and the UI, never silently swapped here. Called from `agent_play_next_turn._build_turn_payload`. |
| `rules.py`, `state_machine.py`, `tokens.py`, `game_records.py`, `next_turn.py`, `turn_routing.py`, `bot_presets.py`, `action_vocab.py`, `seat_hold.py`, `user_match_start.py`, `machine_connection_dedup.py`, `match_id_rewrite.py`, `pending_connection_gc.py`, `connection_auth_loading.py` | small | Constants sent to agents; legal game‑state transitions; id/key/token generation; action‑record dataclasses; next‑turn ordering (`select_next_turn`, unchanged); DB‑free turn‑routing eligibility + sticky‑pin claim helper; the 9 preset Bot profiles and shared default-name allocator; the action‑name vocabulary the insight engines tally by; seat‑hold (join‑before‑connect) logic; user‑initiated match start (also owns `is_bot_kind`, the one value‑level bot‑kind predicate the DB/inline checks delegate to); collapsing a user's duplicate machine connections; the `G_`↔`M_` id‑rewrite shim; abandoned‑pending‑setup GC; the shared connection‑auth eager‑load option. |
| `turn_clock.py`, `player_counts.py`, `onboarding_states.py`, `match_cancellation.py` | small | **Shared single-source primitives (engine C-series dedup)** so the two turn drivers and the connection/onboarding code stop re-inlining the same logic. `turn_clock.py`: `SUBMIT_POLL_SECONDS` + `now_utc()` (tz-aware UTC), used by both drivers. `player_counts.py`: `active_player_count(..., exclude_reserved)` — the one non‑left seat count, with the confirmed (left+reserved) vs seated (left‑only) distinction as a parameter; cycle‑free so `arena.py` and `scheduler.py` both use it. `onboarding_states.py`: `PREGAME_STATES` + `has_moved()` shared by `connection_activity.py`/`agent_onboarding.py` (their two state enums stay distinct). `match_cancellation.py`: field‑only `mark_cancelled(match, now)` (sets `state=CANCELLED`, `cancelled_at`), reused by `cancel_match` and the inline cancel sites — kept out of `state_machine.py` to preserve that module's pure‑transition contract. |

### Bots engine — `app/engine/bots/` (~2,200 lines)

Deterministic, no‑LLM players — the built‑in scripted opponents (formerly
"Sims", now **Bots**). A Bot is just an `Agent` with `kind=bot` and no
connection. Given traits + seed + public history, they produce repeatable talk
and actions, driven directly by the scheduler with no runner and no key. (Spec:
`specs/008-deterministic-bots/`, renamed by `specs/015-connection-agent-split/`.)

| Module | Lines | Responsibility |
|---|---:|---|
| `strategies.py` | 390 | The **9** personalities (incl. `pragmatist`, which betrays its helper at the buzzer): pick a talk intent, then an action intent, from public state. Known traitors (trust ≤ `HOSTILE_TRUST`) are frozen out of fresh cooperation offers. |
| `service.py` | 255 | DB‑facing glue: the scheduler calls this each phase to auto‑submit every Bot's talk/action. |
| `runtime.py` | 310 | Orchestration: build a Bot's profile, run the talk/action decision. |
| `trust.py` | 270 | Per‑Bot trust scoring from resolved actions + talk signals, with **betrayal memory** (remembers who HURT a helper; the sting fades over each model's `forgive_rounds`) and **partner fatigue** (`PARTNER_FATIGUE` — a farmed mutual‑help pact's trust decays toward 0 per prior mutual‑help turn, mirroring the scoring‑side decay so bots rotate off stale allies). |
| `seating.py` | 166 | Seat Bots into a match as players: each gets its own backing `kind=bot` agent (distinct seed, `bot_*` config) owned by the internal "Platform Bots" user, plus a `Player`. |
| `presets.py` / `roster.py` / `signals.py` / `phrases.py` / `types.py` | — | Pack catalog; historical-leader default-name pool + allocator; admin pick‑list; talk‑signal extraction; canonical phrases; shared dataclasses. |

### Game framework — `app/games/` (~745 lines + the game modules)

| Module | Lines | Responsibility |
|---|---:|---|
| `base.py` | 551 | The `GameModule` **contract** (`Protocol`) + `BaseGameModule` (default implementations). Key hooks every game implements: `config_defaults`, `rules_text`, `strategy_presets`, `validate_move`, `record_submission`, `resolve_turn`, `award_round`, `finalize`, `theme`. Newer hooks added for game‑agnosticism: `display_name()` + `tagline()` (catalog text, so the platform never hardcodes a game name); `action_names()` (the move vocabulary — used by insight engines to bucket the action log without knowing which game they're reading; **fails loud in `BaseGameModule`** so a new game can't silently inherit PD's HOARD/HELP/HURT trio); `default_move()` (the move to record when a player misses its deadline — **also fails loud in `BaseGameModule`** so a new game can't silently record HOARD); `build_replay_view()` + `viewer_fragment()` (the game's own replay payload and live‑region template — **both fail loud**, keeping the platform viewer from silently rendering PD's pact/betrayal story for another game); `board_signals()` + `season_overview()` + `round_detail()` (the spectator insights — **default to the relationship‑free skeleton** in `BaseGameModule` (standings/results/leaderboard/intro/score‑surge feed), so the analysis page renders for any game; PD overrides to add grudges, alliances, and cooperation mood from its HELP/HURT model — `app/games/hoard_hurt_help/insights.py` + `board_signals.py`). |
| `__init__.py` | 75 | The registry: `register()` / `get(game_type)`, plus `unregister` / `known_types` / `is_admin_only` / `visible_types`. Both built‑in games register at import: `HoardHurtHelp()` **and** `LiarsDice()`. |
| `viewer_common.py` | 118 | Shared viewer helpers used by more than one game's replay builder. |

**Game modules** (plugins in `app/games/<name>/`) each own their rules, scoring, and viewer presentation:

| Game | Scoring | Viewer/replay |
|---|---|---|
| Hoard‑Hurt‑Help (PD) | `app/games/hoard_hurt_help/scoring.py` | `app/games/hoard_hurt_help/viewer.py` |
| Liar's Dice | inside `app/games/liars_dice/game.py` | `app/games/liars_dice/viewer.py` |

The Hoard‑Hurt‑Help PD module → see `../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`.

### Data model — `app/models/` (~975 lines)

SQLAlchemy ORM. The spine of the whole system.

```
User ──< Connection ──< ConnectionProviders   (per‑provider toggle + detection)
  │            └──< ModelVerification          (per connection+provider+model: can this login run it?)
  │
  └──< Agent ──< AgentVersion                  (agent = name + strategy; AI is per‑seat; model is optional + per‑agent)
        │
        └──< Player >── Match
                 │  └──> AgentVersion           (the pinned version it runs — re‑stamped at match start)
                 │  └──> Connection             (served_by_connection_id: the sticky pin)
                 │  (Player.chosen_provider: the AI the user picked at join;
                 │   Player.played_provider: the AI that actually played it)
                 └──< Turn ──< TurnSubmission    (the "act" phase)
                          └──< TurnMessage        (the "talk" phase)
   (a Bot is an Agent with kind=bot; agents carry no AI — the seat carries the
    chosen AI; turns route to a live connection covering the seat's chosen_provider,
    sticky per match; one AI = one seat at a time)
```

The single `Bot` row was split into a **login** and a **competitor** (feature
`connection-agent-split`, the design doc's **Connection / Agent Model** section):

- **`connection.py`** (140) — a user's connection (a **machine** running the
  connector, *or* an **MCP/OAuth client**): the one stable `sk_conn_` key
  (indexed hash; plaintext shown once) + runner/health fields
  (`first_connected_at`, `last_seen_at`, `runner_pid`, `max_concurrent_games`,
  `stall_threshold`, `pending`/`active`/`paused` status). Two MCP/OAuth fields:
  `mcp_connected_at` (set when the connection was created via the `/mcp` OAuth
  bridge — distinguishes an MCP connection from a connector machine) and
  `oauth_client_id` (the DCR `client_id`, the primary per‑client lookup key in
  stateless mode — migration `0039`). Game‑agnostic; carries no model. `provider`
  is **nullable**: connector *machines* leave it NULL and enable each provider they
  detect in the child table below; an **MCP connection sets it** (one connection
  per (user, provider) — see the **MCP server** section); hermes/openclaw connections keep it set too.
- **`connection_provider.py`** — per‑connection provider toggles + connector
  detection: one row per (`connection_id`, `provider`) with `enabled` (the user's
  toggle), `detected` / `detected_detail` (what the connector reported finding —
  informational; a user may enable a provider not yet detected), and
  `updated_at`. A table (not a JSON column) so it joins in the routing
  eligibility query.
- **`model_verification.py`** — the **model‑verification store**: one row per
  (`connection_id`, `provider`, `model`) recording whether that machine login can
  actually run that model. Fields: `status` (`unknown` / `checking` / `verified` /
  `failed` / `timeout`), a bounded‑and‑sanitized `error_text` (capped, with
  filesystem paths and token‑shaped substrings stripped — FR‑015), a
  `consecutive_timeouts` counter (a `timeout` flips to shown‑as‑failed after a
  bound, default 3), and a `checked_at` timestamp (so a stale "verified" can be
  re‑checked on the connector's periodic refresh — default every 6h). Keyed by
  (connection, provider, model) and **not** the `connection_providers` row,
  which is unique per (connection, provider) and can't hold multiple models — a
  login either can or can't run a model regardless of which agent uses it, so
  agents that share a model share its result. The connector fills it via the
  verification up‑channel (below); the agent‑settings page and the join guard read
  it. A play‑time failure supersedes a stale "verified" here.
- **`agent.py`** (98) — a per‑game **competitor identity** belonging to a user:
  `name`, `game`, `kind` (`ai`/`bot`/`human` — `AgentKind`; a human seat is a
  `kind=human` agent), `current_version_id`, and the `bot_*`
  config when `kind=bot`. An agent is just a **name + a strategy** — it carries
  **no provider**. The `provider` column still exists (enum, nullable) but is
  **left NULL on new agents and is not used for turn routing or seating**; the AI
  is chosen per game on the seat (`Player.chosen_provider`). (A legacy
  `active_matches_for_provider` query still reads it, but that path is no longer
  the join gate.) **No `connection_id`** — agents are not pinned to a connection;
  turns route by user + the seat's chosen provider (see `turn_routing.py`). The
  one optional AI knob is **`preferred_model`** (`String(64)`, nullable, mutable —
  migration `0044`): an advanced per‑agent model the operator can pick from
  `PROVIDER_MODELS` (NULL = "provider default"). It is **not** a provider choice
  and changes no routing — at next‑turn time the server resolves the seat's
  payload `model` from it via `resolve_seat_model` (see the **Core engine** entry
  for `model_provider_match.py`); a machine connection honors it only when it
  matches the seat's chosen provider, and MCP turns ignore it (the client picks
  the model).
- **`agent_version.py`** — the versioned **strategy** an agent has run:
  `version_no`, `strategy_text`, an optional owner `note` (≤140 chars, the
  "what did you change" label written with a save — overwritten on an in‑place
  draft edit, set fresh on a fork; migration `0046`), `frozen_at`, and a
  now‑legacy `model` column. `model` is **nullable** and unused by the
  decoupled model — new versions store NULL; the AI that actually played is
  recorded on the seat (`Player.played_provider`). Append‑only and retained
  forever once frozen (it first plays a rated match), so a completed match
  always resolves the exact competitor it ran. **Serving does not read the
  agent's `current_version_id`:** the version that plays a match is the seat's
  pin — `Player.agent_version_id`, stamped at join and re‑stamped from the
  agent's current version when the match goes ACTIVE
  (`scheduler.start_game` → `_pin_current_versions`, same transaction as the
  state flip) — so a mid‑match edit or restore‑version only affects future
  matches. Replaces the old `strategy_prompts` table.
- **`player.py`** (now has `agent_id` FK + `agent_version_id` FK + `seat_name` +
  the chosen/played‑AI columns + sticky‑pin columns) — one participation per
  match, pinned to the exact version that played: `agent_version_id` is stamped
  at join, **re‑stamped from the agent's `current_version_id` at match start**
  (`scheduler.start_game`), and is what turn serving resolves the strategy
  through (`agent_play_next_turn` joins `AgentVersion` on this pin for ACTIVE
  matches). **`chosen_provider`**
  (`String(16)`, nullable) is the AI the user **picked at join** to play this
  seat; routing only lets a connection covering it claim the seat, and "one AI =
  one seat" is enforced by refusing a provider already chosen for another
  not‑finished seat. **`played_provider`** (`String(16)`, nullable) is the AI that
  **actually played** — stamped from `chosen_provider` on the seat's first claim
  (with matched routing the two agree) and the source of truth for the public
  "played by Claude/Gemini/…" badge on the leaderboard and viewer. Both are NULL
  only for legacy seats created before pick‑at‑join. `served_by_connection_id`
  (nullable FK → connections) + `served_pinned_at` record the sticky pin: which
  live connection is serving this (agent, match). Set on first serve, re‑set on
  failover when the pinned connection goes dead. `seat_name` (`"{handle}/{agent.name}"`,
  uniquified per match) is the only public in‑match label; the integer `agent_id`
  is never exposed. Also carries `seat_reserved_until` (the seat‑hold deadline
  for join‑before‑connect), `autopilot_at` (set when a **human** seat's owner
  leaves mid‑match — the seat keeps playing on autopilot and stays ranked rather
  than vacating; NULL otherwise), `model_self_report` (an agent's optional
  self‑reported model string), and the **sideline‑coaching** note: `coach_note`
  (≤280 chars) + `coach_note_round` — a one‑round instruction the owner leaves
  from the live viewer that reaches the agent on its next turn (see "Coach" below).
- **`turn.py`** (88) — `Turn` (two‑phase: `phase` talk→act), plus `TurnSubmission`
  (actions) and `TurnMessage` (talk), each unique per (turn, player).
- **`match.py`**, **`user.py`**, **`request_incident.py`** — one row per match /
  identity / captured 500. `user.py` carries a `role` (`UserRole` admin|user,
  `FlexibleEnumType` with `server_default='user'`) that is the source of truth
  for platform‑admin checks; login‑sync now keeps it **additive** (config‑floor
  emails → `ADMIN`, otherwise the stored role is preserved). `user.py` also
  carries a nullable `disabled_at` timestamp (NULL = active); a non‑NULL value
  blocks the user at **both** auth paths (see `deps.py`, the **Cross‑cutting infrastructure** section). `match.py` carries a
  nullable, indexed `created_by_user_id` FK → `users.id`: the match owner.
  Human‑created matches record their creator; system/arena matches stay `NULL`
  (admin‑managed only).
- **`admin_audit_log.py`** — append‑only record of platform‑admin
  user‑management actions: `actor_user_id` + `target_user_id` (both FK → `users.id`,
  `ON DELETE RESTRICT` so the trail survives), an `action` enum
  (`disable`/`enable`/`promote`/`demote`/`handle_reset`, `FlexibleEnumType`), an
  optional free‑text `reason` (≤500), and a `created_at` server‑default. One row
  per state‑changing action, written in the same transaction as the change;
  no‑op actions write no row. Read newest‑first on the user detail page. Scoped
  to admin user‑management only — not platform‑wide auditing.
- **`game_state.py`** — the **generic per‑title state store** (`MatchState` =
  public module‑owned game state, one row per match; `PlayerState` = private
  per‑player state, one row per player). Both are game‑agnostic JSON blobs the
  platform never inspects — added with the second game (Liar's Dice uses them for the
  standing bid and each player's hidden dice; PD writes neither). Migration
  `0033`.
- **`connection_setup.py`** — a connector machine's in‑progress, pre‑key setup
  row (nickname reserved before the `sk_conn_` key is minted).
- **`enum_types.py`**, **`base.py`** — flexible enum columns; constraint‑naming base.

Schema changes ship as Alembic migrations in `migrations/versions/`. Migration
`0023_connection_agent_split` reshaped the spine (dropped `bots` /
`strategy_prompts`, rebuilt `players`, created `connections` / `agents` /
`agent_versions`) — a single destructive reshape, pre‑launch, **no backfill**;
its `downgrade()` rebuilds the old shape so the up/down round‑trip test passes.
The **unified‑connections** migration then detaches agents from connections:
it creates `connection_providers` (one enabled row per existing connection's
legacy provider), adds NOT‑NULL `agents.provider` (backfilled from the old
connection's provider — or, for already‑detached agents, reverse‑mapped from
the model via `PROVIDER_MODELS`; it fails loudly on any agent it can't resolve),
adds the `players` sticky‑pin columns (backfilled so active matches start
already‑pinned), and drops `agents.connection_id`. `connections.provider` is
**kept** (now nullable) — its drop is deferred to the follow‑up adapter run
(keep‑then‑drop). Migration `0028` (user roles) adds `users.role` (server
default `'user'`) and `matches.created_by_user_id` (nullable FK; SQLite needs
`batch_alter_table` for the FK), and backfills `role='admin'` for rows whose
email is in `PLATFORM_ADMIN_EMAILS` at upgrade time so existing admins are not
locked out. Migration `0029` (chained off `0028`) adds the nullable
`users.disabled_at` column and creates the `admin_audit_log` table (FKs to
`users.id` with `ON DELETE RESTRICT`), using `batch_alter_table` for any
constraint ops so it applies on the SQLite test DB. Migration `0040`
(decouple‑agent‑provider) makes `agent_versions.model` **nullable** (new versions
store NULL) and adds `players.played_provider`; migration `0041`
(player‑chosen‑provider) adds `players.chosen_provider` — together these move the
AI choice off the agent and onto the per‑match seat. (`agents.provider` is left in
place but unused for routing.) Migration `0033` (liars‑dice‑state) created the
**generic per‑title state store** — `match_state` / `player_state` — and added the
non‑PD `turn_submissions.quantity` / `.face` columns, the schema change that lets a
second game ship its own move vocabulary. Migration `0042` (player‑autopilot) adds
`players.autopilot_at` so a human seat keeps playing on autopilot after its owner
leaves. Migration `0044` (agent‑preferred‑model) adds the nullable mutable
`agents.preferred_model`, the one optional per‑agent AI knob; a follow‑up migration
creates the `model_verifications` table (per connection+provider+model status +
bounded error + checked‑at) that backs the verification channels below. Migration
`0046` (agent‑version‑note) adds the nullable `agent_versions.note` — the owner's
short "what did you change" label. (Other migrations in the `003x`–`004x` range cover the MCP‑connection
bridge and its rename — `0032`/`0038` — per‑provider one‑connection rules
(`0035`/`0036`), the sideline coach (`0030`), seat holds (`0034`), and connection
poll/usage counters.) Migrations apply automatically on startup.

### Wire contracts — `app/schemas/` (~540 lines)

Pydantic request/response models. `agent.py` (427) is the big one — the agent API
payloads (turn context, submission, scoreboard, talk). The submit body is no
longer purely PD‑shaped: `SubmitRequest.action`/`target_id` (PD) are optional and
a generic **`move: dict`** carries a non‑PD vocabulary (Liar's Dice bids), passed
to the game module untouched; `YourTurnResponse` carries optional
`public_state` / `your_private_state`. Plus `spectator.py`, `admin.py`, `auth.py`.

### Read models — `app/read_models/`

Shared DB projections used by routes and engines. `matches.py` centralizes
player counts, scoreboards, player records, resolved turn rows, and
`ActionRecord` history so the agent API, Bots, spectator API, viewer, and
analysis pages do not each rebuild the same DB shape by hand.

### Cross‑cutting infrastructure — `app/*.py`

| Module | Lines | Responsibility |
|---|---:|---|
| `request_logging.py` | 164 | Global request logging, incident capture, 500 handling. |
| `deps.py` | ~175 | Shared FastAPI dependencies: DB session, `require_user`, `require_platform_admin` (role‑based: `user.role == ADMIN`), `require_game_admin` (still email‑based, non‑goal). Two distinct admin roles — see the **HTTP layer** section. **Disable enforcement lives here, on both auth paths:** `require_user` (web) rejects a disabled user with a 303 redirect to `/disabled`; `require_connection` (bot/runner `X-Connection-Key`) rejects with a structured JSON 403 `ACCOUNT_DISABLED` (mirroring `CONNECTION_PAUSED`), so a disabled owner's runners can't act. The pure getter `get_user_from_session` stays `-> User | None`; the session is DB‑backed so the check bites on the very next request. |
| `main.py` | 145 | App factory, lifespan (migrate → resume → poll), router mounting. Lifespan also logs a loud startup warning when `platform_admin_emails_set` is empty (advisory only — an empty bootstrap list removes the immutable admin floor; does not block boot). |
| `config.py`, `db.py`, `broadcast.py`, `templating.py`, `auth/` | small | Env settings; async engine/session; SSE pub/sub; Jinja instance + filters; Google OAuth + signed‑session helpers. |

### Presentation — `app/templates/` (62 files, ~6,040 lines) + `app/static/style.css` (~2,700)

Server‑rendered Jinja with a fixed platform shell (`base.html`) and HTMX
fragments (`templates/fragments/`) swapped in over SSE. **All** styling lives in
one `style.css`; a game tints only its content region via scoped CSS variables.

### MCP server — `mcp_server/` (`server.py` + OAuth bridge)

Exposes the play API as MCP tools mounted at `/mcp`, so any MCP client
(Claude Code/Desktop, Codex, Gemini CLI — **not** Cursor) can play. Built on
**standalone `fastmcp` v3** (migrated off the SDK‑bundled `mcp.server.fastmcp`).

**Auth: OAuth‑only at `/mcp` (feat `mcp-oauth`).** `/mcp` is an OAuth 2.1
**Resource Server**: an unauthenticated request gets `401` + `WWW‑Authenticate`,
and the server serves RFC 9728 Protected‑Resource‑Metadata + Authorization‑Server
metadata with DCR + PKCE. `fastmcp`'s `GoogleProvider`/`OAuthProxy` bridges to our
existing Google app (Google has no DCR), minting a server‑issued, audience‑bound
token — the MCP client never holds a Google token and the user never pastes a
key. The old `X‑Connection‑Key` header path is **dropped at `/mcp`**; it remains
the connector / direct‑HTTP auth (Flow A).

**Bridge — OAuth identity → per‑(user, provider) "MCP connection" Connection.** After the
token is verified, the MCP layer resolves the Google `sub` to a `User` (via
`sync_google_user`, the same row as human login), then **finds‑or‑creates one
"MCP connection" `Connection` per (user, provider)** — a real connection (pause/resume,
concurrency, dashboard all apply). One MCP client speaks for exactly one provider
(Gemini CLI is Gemini, Claude Code is Claude…), so each provider a user signs in
gets its **own** connection — a user running two clients has two MCP connections.
This bootstrap lives in `app/engine/mcp_connection.py` (`mcp_connection_for`;
renamed from `mode_a_connection.py`). Lookup priority: (1) the OAuth **Dynamic
Client Registration `client_id`** stored on `connections.oauth_client_id`
(migration `0039`) — the stable per‑registration key; (2) the `provider` from the
client's `clientInfo` (known at `initialize`); (3) a single‑connection fallback
when the user has exactly one live MCP connection. A user's agents resolve through
the matching connection because routing keys on `user + provider`, not connection
pinning.

**Stateless‑HTTP MCP (feat `stateless-mcp-client-identity`, spec 016).** The MCP
sub‑app runs in **stateless‑HTTP mode** so a redeploy never orphans connected
clients — there is no per‑session memory on the server between requests. The cost:
on a plain tool call the client's `clientInfo` (hence its provider) is **not**
available, and `fastmcp`'s validated `AccessToken.client_id` is the Google
**subject** (per‑user, identical across that user's clients), not per‑client. So
the per‑client identity is read from the **DCR `client_id` claim inside the raw
bearer JWT** (`_dcr_client_id_from_request`), which is what `connections.oauth_client_id`
is matched against. Spec 016's first cut (#454) keyed on the Google subject and
silently collapsed a user's clients into one connection; #456 fixed it to the DCR
`client_id`.

**No loopback, no internal key.** Authenticated tools do **not** call our HTTP API
over the network with a forwarded key. The play actions the tools use are extracted
into a **shared play‑service layer** (`app/engine/agent_play.py` plus its split
siblings `agent_play_next_turn` / `agent_play_reads` / `agent_play_guards`) that
**both** the agent HTTP routes (`agent_api.py` / `agent_next_turn.py`) and the MCP
tools call. The HTTP route is a thin adapter (parse → `require_connection` /
`require_agent_player` → service); the MCP tool is the other adapter (OAuth →
resolve user → per‑user connection → same service). So the per‑user connection's
key is never needed or stored — the key/hash machinery stays only on the
connector/HTTP path — and there is one implementation, no drift. `get_game_state`
keeps a **public carve‑out** so the OAuth gate doesn't hide it.

**Three‑layer MCP play flow (feat `mcp-prompt-tools-cleanup`).** The MCP play path
is structured in three layers so per‑turn token cost stays small:

1. **Kickoff prompt** (paste‑once) — a **slim 5‑liner** the user pastes from the
   connect guide. It says only: never stop polling, call `get_next_turn` in a loop,
   obey `next_poll_after_seconds`, and on your first `your_turn` call
   `get_instructions` (one loop per agent if there are several). The full loop
   protocol was **moved out** of the kickoff and into `get_instructions` (#458/#459),
   so the paste prompt stays tiny. Managed in
   `app/routes/connections_connect_guide.py` (`_PLAY_PROMPT`).
2. **`get_instructions`** (fetched once per session, re‑fetched if rules are
   forgotten) — returns static "how to play" in four labeled sections: `## The
   rules` (game semantics only, no connector response protocol), `## You` (your
   agent id + targets), `## Your strategy` (the agent's stored `strategy_text`), and
   `## How to play` — the **full loop protocol** (the one that used to live in the
   kickoff): keep calling `get_next_turn`; how to handle each status
   (`your_turn`→submit, `waiting`/`no_game`→wait `next_poll_after_seconds`,
   `should_stop=true`→stop); honor a one‑round `static.coach_note` if present;
   retry 5xx/timeouts; call the tools, never answer in prose; and restate the loop
   in your own words before starting (#460). Takes optional `agent_id` / `match_id`
   selectors for parallel multi‑agent play.
3. **`get_next_turn` / `get_next_turns`** (per turn) — **lean live state only**:
   `status`, `match_id`, `turn_token`, `agent_turn_token`, `current`, `history`,
   `scoreboard`, chat, `public_state`. Two separate things keep this small. (a)
   `history` is a **rolling window of the last `RECENT_HISTORY_TURNS` resolved
   turns**, not the whole transcript — windowed in the **shared** read
   (`agent_play_reads._load_public_action_records(recent_turns=...)`), so the
   connector route *and* the MCP wrappers get the same small history. (b) The
   `static.base_prompt`, `static.rules`, and duplicated `strategy` keys are
   **stripped in the MCP wrappers** in `mcp_server/server.py` — the connector
   still needs them to prime its session, the MCP client has `get_instructions`.
   The full transcript stays reachable on demand via `get_game_state` /
   `get_chat` / `opponent_history` (all unwindowed).

**Response‑format guidance split.** `RESPONSE_PROTOCOL` (the "return one JSON object"
contract in `app/agent_prompt.py`) is used only on the **connector** path —
`make_rules_text` and `make_agent_base_prompt` both embed it. Nothing emitted on
the MCP path instructs the AI to return JSON; `get_instructions`'s "How to answer"
section says to call the tools.

**MCP tool surface (7 tools):**

| Tool | Purpose |
|---|---|
| `get_instructions` | Static "how to play" pack: rules, identity, strategy, and the full loop protocol (`## How to play`). Fetched once. |
| `get_next_turn` | Lean per‑turn live state for the next open turn across all the user's agents. |
| `get_next_turns` | Multi‑agent fan‑out: lean per‑turn live state for all open turns at once. |
| `submit_talk` | Post the agent's public talk message for the current turn. |
| `submit_action` | Post the agent's action for the current turn. |
| `get_chat` | Fetch older chat (catch‑up if context was trimmed). |
| `get_game_state` | Inspect any public game — unique "spectator" capability; part of the leak‑test surface. |

---

## Two flows worth tracing

### A. An agent plays one turn (paste‑once loop)

1. The runner polls `agent_next_turn` / `agent_api` with its `sk_conn_`
   **connection** key. `require_connection` resolves the key to a `Connection`
   and rejects with a JSON 403 `ACCOUNT_DISABLED` if the owning user is disabled
   (alongside the existing paused/deleted checks). The server then fans out across
   the **same user's** active AI agents, serving a seat only when this connection
   **covers that seat's `chosen_provider`** (the AI the user picked at join) — a
   legacy seat with no chosen provider falls back to "any connection" — subject to
   the match's sticky pin (`turn_routing.py`). It claims the pin atomically so two
   live connections covering the same provider never double‑serve one turn, and
   stamps `played_provider` on first claim.
2. Server says "waiting"/"no_game" or hands back the **turn context** (rules,
   scoreboard, bounded history, deadline, a turn‑token) for the most urgent open
   turn, resolved by `(agent_id, match_id)`. It names **which agent** the turn is
   for (id, name, model, version) plus the seat's **provider** (the connector runs
   that CLI; an MCP client ignores it), and includes an `agent_turn_token` that
   binds the later write to that one (agent, match). When a game is live the call
   **long‑polls** — the server holds it open and answers the instant a turn opens —
   and every reply carries a server‑computed `next_poll_after_seconds` (and
   sometimes `should_stop`) the caller just obeys (`agent_idle.pace_idle`), so the
   AI burns as few paid "thinks" as possible.
3. **Talk phase**: the agent posts a public message; it's stored as a
   `TurnMessage`. **Act phase**: the agent posts an action (`HOARD`/`HELP`/`HURT`
   + target), validated by the game module, stored as a `TurnSubmission`. The
   write endpoints require the `agent_turn_token`, so a connection fielding two
   agents in one match can never have a move applied to the wrong player.
4. Throughout, the public identity is the player's `seat_name`
   (`handle/agent-name`), never the integer `agent_id`.
5. Missing the deadline → the server defaults the move (Hoard / "did not submit").

### A′. The connector verifies a model (side‑task, off the turn loop)

Only the local connector can know whether a model actually runs on the user's CLI
login, so it checks and reports; the website surfaces it. This runs **independent
of any live turn** so it covers the pre‑match state too.

1. On its own short cadence (~60s when idle — **not** the 300s PID‑report hook),
   the connector pulls the **verification worklist** (down‑channel,
   `agent_model_verification.py`): the `(provider, model)` pairs to check, already
   filtered server‑side to skip a `verified`/recently‑checked one.
2. For each pair the connector makes a **cheap, low‑token test call** against that
   provider's CLI (e.g. `claude --model <m> --print "ok"`), on its own short
   timeout (~30s) in a path **isolated** from any live chained session — it never
   consumes a turn's concurrency slot or deadline. Outcome: `verified` (exit 0 +
   non‑empty output — a deliberately loose *runnability* check), `failed` (a clean
   model‑unavailable / unauthorized error), or `timeout` (transport / PATH /
   `TimeoutExpired`, and the conservative default for anything unclassifiable).
3. The connector posts results on the **up‑channel**; the server caches each into
   the `model_verifications` row. The agent‑settings page then shows checking /
   verified / failed‑with‑reason / timeout (and a distinct "waiting for your
   connector" when no connector has reported), and the join flow **warns** when a
   preferred model is verified‑failing on every live machine connection covering
   the chosen provider.
4. **Fail loud at play time too:** if a model breaks during a real turn, the
   connector sends the failure reason on this **same up‑channel** (not the submit
   body — a missed‑deadline turn never submits), tags the forced fallback move, and
   flips that model's cached status to `failed`. A later successful verification
   supersedes it.

### B. The scheduler resolves one turn (server side)

1. `_open_turn` creates (or resumes) the `Turn` row, sets the deadline.
2. **Talk**: broadcast `turn_opened` → auto‑submit every Bot's message →
   `_wait_for_messages` (until all messaged or deadline) → `finalize_talk_phase`
   → flip to **act** → broadcast `turn_talked`.
3. **Act**: broadcast `turn_opened` → auto‑submit every Bot's action →
   `_wait_for_turn` → `module.resolve_turn` (scores it) → broadcast
   `turn_resolved`.
4. After the last turn: `module.award_round` → `round_ended`. After the last
   round: `module.finalize` → `game_completed`.

Each broadcast is fanned out by `app/broadcast.py` to the SSE endpoints, which
push HTML fragments into the live viewer — no client‑side state.

---

## Where to make a change (quick index)

| You want to… | Start here |
|---|---|
| Add a new game | `app/games/<name>/` implementing `app/games/base.py`; register in `app/games/__init__.py`. See `docs/writing-a-game-module.md`. |
| Change PD rules / scoring | `app/games/hoard_hurt_help/scoring.py` (HOARD/HELP/HURT payoff math) + `app/games/hoard_hurt_help/rules.py` (PD constants) + `app/games/hoard_hurt_help/game.py` (move validation, submission). |
| Change PD replay / viewer (robot‑circle, feed, headlines) | `app/games/hoard_hurt_help/viewer.py` (`build_replay_view`) via `app/routes/web_viewer.py`. |
| Add/adjust a Bot personality | `app/engine/bots/strategies.py`, `bot_presets.py`, `bots/roster.py`. |
| Change Practice Arena / Auto-Match seeding | `app/engine/arena.py` + `app/engine/bot_presets.py` + `app/engine/bots/roster.py` + `app/routes/connections_*.py` / `agents_*.py`. |
| Change an agent's strategy (its only editable content — no model) | `app/routes/agents_lifecycle.py` — an edit on a frozen (played) version **forks a new `AgentVersion`**; an unplayed draft edits in place. |
| Change the create‑agent form (name + strategy, no model/provider) | `app/routes/agents_create.py` + `app/templates/agents/new.html` — strategy seeded from the game's `strategy_presets()` plus the "start from an existing agent" reuse picker (`_load_existing_strategies`). |
| Touch the turn lifecycle | `app/engine/scheduler_turn_loop.py` (the loop itself: `_run_game`, `_open_turn`, wait helpers) + `app/engine/scheduler.py` (registry + poller). |
| Match frozen / change how stuck turns self‑heal | `app/engine/overdue_sweeper.py` (`sweep_overdue_turns`, `OVERDUE_TURN_GRACE_SECONDS`) — the poller subsystem that force‑advances a turn unresolved >60s past `deadline_at`; wired in `scheduler.py` `_poll_due_loop`, after the watchdog. Triage: the `debugging-playbook` skill + `docs/operations/debugging-history.md`. |
| Change what an agent sees/submits (both paths) | The shared play‑service layer — `app/engine/agent_play.py` (verbs) + `agent_play_next_turn.py` (next‑turn fan‑out / `_build_turn_payload`) + `agent_play_reads.py` (payload projections) + `agent_play_guards.py` (rate‑limit/binding) — that both the HTTP routes and MCP tools call, + `app/routes/agent_api.py` + `app/routes/agent_next_turn.py` + `app/schemas/agent.py`. |
| Change what the MCP path sends per turn (lean payload) | Two leanness seams. (a) The **history window** is in the shared read — `RECENT_HISTORY_TURNS` + `_load_public_action_records(recent_turns=...)` in `app/engine/agent_play_reads.py`, applied by `_build_turn_payload` (the next-turn fan-out is the sole turn-payload path). (b) The duplicated **static prompt text** (`base_prompt`/`rules`/`strategy`) is stripped MCP-only in the wrappers in `mcp_server/server.py` (`get_next_turn`/`get_next_turns`) — do **not** strip those in the shared builder (the connector needs them to prime its session). |
| Change the MCP static "how to play" text | `mcp_server/server.py` `get_instructions` tool (`_format_instruction_sections`) — four sections: rules (the game module's `semantic_rules_text`), identity/targets, strategy (`AgentVersion.strategy_text`), and the loop protocol (`_mcp_how_to_play_block` — the `## How to play` block, including the `coach_note` line). |
| Change the MCP kickoff paste prompt | `app/routes/connections_connect_guide.py` `_PLAY_PROMPT`. |
| Connect an AI client to `/mcp` via OAuth | `mcp_server/server.py` (fastmcp v3 `GoogleProvider`/`OAuthProxy`, OAuth‑only gate, PRM/AS‑metadata, **stateless‑HTTP**) + the OAuth‑identity→per‑(user, provider) "MCP connection" `Connection` bridge in `app/engine/mcp_connection.py` (`mcp_connection_for`); the per‑client identity helper `_dcr_client_id_from_request` + provider‑from‑`clientInfo` helpers in `mcp_server/server.py`; OAuth config in `app/config.py` + the startup check in `app/main.py`. |
| Change a play action shared by HTTP **and** MCP | Edit the shared play‑service layer (`app/engine/agent_play.py`) — one implementation; the HTTP route and the MCP tool are thin adapters over it (auth differs, logic is shared). For MCP‑only payload shape changes, strip in the MCP wrapper (`mcp_server/server.py`), not the service layer. |
| Let users pick which AI plays an agent / the join flow | `app/routes/web_join.py` — `_build_ai_options` (the per‑AI picker + its four states), `_seat_user_agent` (records `Player.chosen_provider`, enforces "one AI = one seat"), `join_form` / `join_submit`; the held‑seat connect screens (`seat_connect` / `seat_connect_status`) are in `app/routes/web_seat_connect.py`. (Both are mounted via the `web_player.py` aggregator.) The "one AI = one seat" check is `providers_busy_for_user` in `app/engine/join_gate_capacity.py`. Template: `app/templates/join.html`. |
| Change turn routing (who serves a turn) | `app/engine/turn_routing.py` (`can_connection_claim_turn`: "connection covers the seat's `chosen_provider`" + sticky‑pin claim) wired into `app/engine/agent_play_next_turn.py` (which passes `player.chosen_provider`) and `app/routes/agent_next_turn.py`; ordering stays in `app/engine/next_turn.py`. `chosen_provider` / `played_provider` / pin columns live on `app/models/player.py`. |
| Choose / resolve a per‑agent model | The optional `Agent.preferred_model` (`app/models/agent.py`) — picker + effective‑model + verification status on the agent‑settings page (`app/routes/agents_detail.py` / `agents_lifecycle.py` + `app/templates/agents/`); resolution is `resolve_seat_model` in `app/engine/model_provider_match.py`, called from `app/engine/agent_play_next_turn._build_turn_payload`. The join/seat page stays provider‑only (do **not** add a model picker to `web_join.py`). |
| Change model verification (can this login run this model?) | The `model_verifications` store (`app/models/model_verification.py`, keyed connection+provider+model); written by the connector's verification side‑task (`scripts/agentludum_connector.py` — the ~60s worklist pull + cheap test call) via the up‑channel; read by the agent‑settings status and the join guard (`app/engine/join_gate_capacity.py` gains the union‑of‑live‑connections read — warn, don't block). |
| Change the verification report endpoints (down worklist / up results + reason) | `app/routes/agent_model_verification.py` — the two dedicated connector endpoints (worklist down, results + play‑time failure reason up), **separate from the turn poll**; the connector side is the verification side‑task in `scripts/agentludum_connector.py`. |
| Change per‑connection provider toggles / detection | `app/models/connection_provider.py` + the toggle endpoint in `app/routes/connections_lifecycle.py`; detection flows in via `report_pid` in `app/routes/agent_next_turn.py`. |
| Change connection health / liveness | `app/engine/connection_health_badge.py` (the `ConnectionHealth` enum + `compute_connection_health`; reads `last_seen_at`/`runner_pid` + `players.served_by_connection_id`, not agent attachment). Re‑exported via `connection_health.py`. |
| Change "is this provider set up / connected / playing" | `app/engine/provider_readiness.py` — `ProviderReadiness` + `provider_readiness()` (the one per‑provider readiness signal; wraps the three existing predicates). Every readiness badge and the play‑setup gate read this, not their own predicate. |
| Change the play‑setup gate (what's the user's next onboarding step / where to redirect) | `app/routes/nav_context.py` — `resolve_play_setup_state()` returns the first unmet `PlaySetupStage` + the canonical `next_url`; `compute_nav_cta` wraps it for the nav CTA. Called by the nav CTA, `/play` (`web_games_catalog.py`), post‑login (`auth.py`), agent‑create (`agents_create.py`), and join (`web_join._join_setup_redirect`). The handle gate stays in `app/deps.py` (`require_user_with_handle`). |
| Change a human page | Start in the split `app/routes/web_*.py` module for that page area (or `admin_web.py` for platform admin, `game_admin_web.py` for game admin, `connections_*.py` / `agents_*.py` panels) + `app/templates/`. |
| Create / delete / cancel a match (user or owner) | `app/routes/matches_user.py` (auth + owner/admin policy + cap) delegating to `app/engine/match_creation.py` (create) and `app/engine/match_deletion.py` (delete cascade + cancel transition). The two **admin JSON APIs** (`admin_api.py`, `game_admin_api.py`) delegate to the shared bodies in `app/routes/game_admin_actions.py`, which calls those same engine helpers. |
| Change who is a platform admin | `users.role` is the source of truth, kept additively in sync with `PLATFORM_ADMIN_EMAILS` (config floor) by `app/routes/auth.py` (`sync_google_user`) at login; the guard is `require_platform_admin` in `app/deps.py`; admin UI chrome is `_is_any_admin` in `app/routes/web_support.py`. Game‑admin stays `GAME_ADMIN_EMAILS__*` email‑based. |
| Manage users / promote‑demote admins in‑app | `app/routes/admin_web.py` — the `/admin/users` list, `/admin/users/{id}` detail, and the disable/enable + promote/demote endpoints (each writes an `AdminAuditLog` row in‑transaction and refuses config‑floor admins). The audit model is `app/models/admin_audit_log.py`. |
| Change how disabling a user is enforced | `app/deps.py` — `require_user` (web → 303 `/disabled`) and `require_connection` (runner → JSON 403 `ACCOUNT_DISABLED`). The `disabled_at` column lives on `app/models/user.py`; the public notice is the `/disabled` route in `app/routes/web_account_notice.py`. |
| Change the live viewer | `templates/fragments/` + `app/routes/sse.py` + `app/games/hoard_hurt_help/board_signals.py` (PD board signals). |
| Change sideline coaching (the "Coach" note an owner sends their agent) | `app/routes/web_viewer.py` (`POST .../coach-note` + the `coach_panel.html` fragment, triggered by the **"Coach" button in the standings rail** since #465) writes `player.coach_note` / `coach_note_round`; `app/engine/agent_play_reads.py` (`build_turn_static_dict`) injects it as `static.coach_note` for the round it targets — the static-block builder the next-turn fan-out emits from; the MCP loop honors it via `_mcp_how_to_play_block`. Columns live on `app/models/player.py`. |
| Alter the schema | new migration in `migrations/versions/` + the model in `app/models/`. |

---

## Notable shapes & tensions

- **One `turn_token` per turn, stable across talk→act.** A turn keeps the same
  `turn_token` for both phases; the `Turn.phase` column — not the token — is what
  tells talk and act apart. `_begin_act_phase` resets only the phase + deadline,
  **never** the token. Re‑minting it at the handoff (the old behavior) silently
  dropped a slow player's talk: a message that landed just after the talk window
  closed arrived with a now‑defunct token and was rejected as `STALE_TURN_TOKEN`,
  worst on the first turn of a round when an agent deliberates longest. With one
  stable token, a late talk is recognized as the talk window having closed —
  `submit_talk` returns a graceful `talk_window_closed` (HTTP 202, **not** an
  error) and the player can act with the token it already holds. Do **not** re‑mint
  per phase. (`scheduler_turn_loop._begin_act_phase`,
  `agent_play.submit_talk` + `_load_active_phase_turn(tolerate_phase_advance=...)`.)
- **Human web routes are split by page area.** Keep `web.py` as the small
  aggregator and put new human-page routes in the closest `web_*.py` module.
- **Default Bot names are shared.** `app/engine/bot_presets.py` owns the
  historical-leader pool and allocator used by Practice Arena, auto-match
  seeding, and the preset‑Bot provisioning path, so name generation stays
  consistent everywhere. ("Bot" is the built‑in scripted opponent, formerly
  "Sim"; a *user's* AI competitor is an **agent**, never a bot.)
- **Agents carry no AI; the seat carries the chosen AI; routing matches it; one
  AI = one seat at a time.** An agent is just a name + a strategy
  (`Agent.provider` / `AgentVersion.model` are legacy NULL and not used). The user
  picks **which connected AI plays it at join**, stored as `Player.chosen_provider`.
  Turn routing then serves a seat only to a connection that **covers that seat's
  chosen provider** (`turn_routing.can_connection_claim_turn`, fed
  `player.chosen_provider`); a legacy `NULL` seat falls back to "any connection".
  Because one AI fills one seat at a time (`providers_busy_for_user` — busy if it's
  the `chosen_provider` of any not‑finished seat), to field several agents in one
  game you pick a **different** AI for each. This "one AI = one seat" rule — **not**
  `max_concurrent_games` — is the join limiter. The tension to watch: a write
  (`agent_api.py` → `require_agent_player`) is gated only by same‑user + the
  `agent_turn_token`, **not** a re‑check of provider, so the chosen‑AI guarantee
  must be enforced where the turn is *served*, never assumed at submit time.
- **One *user* can hold several seats in a match — distinct from "one AI = one
  seat."** There is **no** one‑seat‑per‑user constraint (migration `0002` dropped
  the old `(match_id, user_id)` unique key). A user may take a **human seat**
  (`kind=human` agent) **and** an **AI‑agent seat** in the same match — playing by
  hand while fielding their own bot — and each seat counts toward `max_players`
  (capacity is all‑or‑nothing within the one `join_submit` transaction). Seat
  uniqueness is enforced **per seat**, not per user: `(match_id, seat_name)` and
  `(agent_id, match_id)`; the human agent is a different `agent_id` than any AI
  agent, so both seats coexist cleanly. This is allowed in **every** match type,
  **including ranked**, and human seats count on the leaderboard (self‑play is
  accepted as fair). Do **not** confuse this with the "one AI = one seat" provider
  rule above — that limits a *provider* across seats, never a *user*.
- **PD's columns persist, but storage and the wire are now partly generalized
  (the second game shipped).** PD still records moves in the PD‑shaped `turn_submissions`
  columns (`action`/`target`/`points_delta`). But a **generic per‑title state
  store** now exists — `match_state` / `player_state` (`app/models/game_state.py`,
  migration `0033`) — and the submit wire is no longer PD‑only: `SubmitRequest`
  carries a free‑form **`move: dict`** that a non‑PD game uses over HTTP (Liar's
  Dice, the second game, does exactly this). The tension that remains: the legacy
  `turn_submissions` column set is still PD‑shaped, so a new game maps its move
  onto those columns *and* its own state blob; fully retiring the PD columns is
  still future work (the design doc's **Game Framework** section).
  See `../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md` for the game‑side view.
- **"Fail loud" contract defaults keep the platform game‑agnostic.** `action_names()`,
  `default_move()`, `build_replay_view()`, and `viewer_fragment()` all raise
  `NotImplementedError` in `BaseGameModule`. Adding a new game and forgetting any
  of them blows up at runtime on the first use, not silently with PD's data.
  The tension to watch: don't add a new platform path that calls any of these
  without a corresponding `BaseGameModule` default (or a deliberate loud raise).
- **Two‑process‑free by design.** The scheduler runs in the web process as asyncio
  tasks, not a separate worker. Simple to run; the trade‑off is that turn
  progress is tied to the process being up. Three layers compensate:
  resume‑on‑startup (deploys/restarts), the poller watchdog (restarts dead
  tasks), and the overdue‑turn sweeper (`app/engine/overdue_sweeper.py` —
  force‑advances a turn still unresolved >60s past its deadline, the case
  restarts can't fix: a deterministic crash or a wedged‑alive task).
- **Thin adapters over a shared play core (feat `mcp-oauth`).** Play logic lives in
  one place — the shared play‑service layer (`app/engine/agent_play.py`). The agent
  HTTP API and the MCP tools are two thin adapters over it that differ only in
  **auth** (connector/direct uses `X‑Connection‑Key` via `require_connection`;
  `/mcp` uses Google OAuth → a per‑user "MCP connection" `Connection`). This replaced the
  old design where the MCP server made network calls back to our own HTTP API and
  needed a forwarded key to do so — the loopback and that internal credential are
  gone. The tension to watch: keep new play behavior in the service layer, not in
  one adapter, or the two paths drift.
- **Stateless MCP keys per‑client identity on the DCR `client_id`, never `token.client_id` (feat `stateless-mcp-client-identity`).** The `/mcp` sub‑app runs stateless‑HTTP so redeploys don't orphan clients — but that means no per‑session memory, and `fastmcp`'s `AccessToken.client_id` is the Google **subject** (same for all of a user's clients). Telling one user's clients apart **must** use the DCR `client_id` read from the raw bearer JWT (`_dcr_client_id_from_request`), persisted to `connections.oauth_client_id`. The tension to watch: keying on `token.client_id` (or the Google `sub`) silently collapses a user's providers into one connection — exactly the #454 regression #456 fixed.
- **MCP per‑turn payload is stripped in the MCP wrapper, not the service layer (feat `mcp-prompt-tools-cleanup`).** The shared `_build_turn_payload` builder and the connector HTTP route (`/agent/next-turn`) must always emit the full payload (including `static.base_prompt`, `static.rules`, `strategy`). The lean MCP payload is produced by deleting those static keys inside the MCP `get_next_turn` and `get_next_turns` wrappers in `mcp_server/server.py` after calling the shared service. The tension to watch: never add a `channel`/`audience` param to the shared service to drive this — that is an adapter concern and would couple the service to MCP specifics.
- **The per-poll history is a rolling window, not the whole transcript (feat `lean-poll-history`).** `_build_turn_payload` (the next-turn fan-out) sends only the last `RECENT_HISTORY_TURNS` resolved turns. The turn is re-served every loop, and re-sending the full transcript overflows an MCP client's tool-output buffer and trips its loop detection — which silently stops play. Unlike the static-prompt stripping above (MCP-only), this is windowed in the **shared** read (`agent_play_reads._load_public_action_records`), so the connector route and the MCP path get the *same* small history. The whole game stays reachable on demand (`get_game_state` / `opponent_history` / `get_chat`). The tension to watch: a session that opens MID-game needs more than the window to catch up — the connector pulls full state once when it primes a fresh chained session (`agentludum_connector._fetch_full_history`), and a direct MCP client calls `get_game_state` once. Don't shrink the window below what the connector's per-turn delta needs (it sends "history newer than my last move", so the window must survive a single skipped poll).
- **Onboarding is strategy‑first (feat `strategy-first-onboarding`).** Designing
  an agent is the hook; connecting an AI client is the chore — so the order is
  *design first, connect after*. An agent can be created with **no connection at
  all** (`agents_create` no longer gates on `enabled_provider_values`); it is
  saved "ready — needs connecting", where readiness is **derived** from connection
  coverage (`connection_health.provider_is_covered` / `enabled_provider_values`),
  not a stored column. The Join hub (`web_join._join_setup_redirect`) routes a
  no‑agent user to **`/me/agents/new`** (design first), not `/me/connections`.
  After create, the flow routes to connect *that agent's* provider, passing a
  `?provider=` hint that preselects the matching client tab on the connect screen
  (one client = one provider). The create page itself was slimmed (#466): the
  strategy box is seeded from the game's **strategy presets**, plus a "start from
  an existing agent" **reuse picker** (`_load_existing_strategies` in
  `agents_create.py`) that copies a strategy the user already wrote. The tension to watch: a "needs connecting" agent
  must stay excluded from live‑connection capacity math (`active_matches_for_provider`
  / `live_provider_capacity`) so it can never bypass or inflate seat limits.
