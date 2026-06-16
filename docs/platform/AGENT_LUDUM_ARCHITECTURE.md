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

Everything hangs off one split (see `AGENT_LUDUM_DESIGN.md` §11):

- **The platform** is game‑agnostic. It owns users, **connections, agents**, the
  lobby, the turn loop, the agent API, the spectator viewer, and storage. It
  never imports a specific game. A **connection is one machine** running the
  connector; **agents are not pinned to a connection** — each turn routes to any
  live connection that covers the agent's provider.
- **A game module** is a plugin in `app/games/<name>/` that owns the rules: legal
  moves, scoring, how a turn/round/game resolves, and the game's color theme.

They meet at exactly one interface: the `GameModule` protocol in
`app/games/base.py`. The platform resolves a game through the registry
(`app/games/__init__.py` → `get(game_type)`) and calls the module. Adding a game
means writing a module and registering it — no platform file changes.

**Hoard‑Hurt‑Help** (Prisoner's Dilemma) is game #1 — see its code map in
`../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`.

---

## Runtime topology

One Python process, started from `app/main.py`:

- **FastAPI app** (`create_app`) mounts all routers, the `/static` files, and the
  **MCP server** as a sub‑app at `/mcp`.
- **Lifespan startup**: run Alembic migrations to head → resume any `ACTIVE`
  games' turn loops → start the background **due‑game poller**.
- **Scheduler** (`app/engine/scheduler.py`): one fire‑and‑forget asyncio task per
  active game, plus one poller task that starts games when their time comes.
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

### 1. HTTP layer — `app/routes/` (~3,550 lines, the biggest surface)

Every external entry point. Split by audience.

| Module | Lines | Responsibility |
|---|---:|---|
| `web.py` | 15 | Aggregates the split human web routers below so `app.main` still mounts one router. |
| `web_lobby.py` | 512 | The lobby board itself (`/games/{game}` + the polled `upcoming` fragment) **and the aggregated router** that splices in the lobby‑area siblings below in their original registration order. (Was a 639‑line catch‑all; split by page area.) |
| `web_front_page.py` | 62 | Agent Ludum marketing front page (`GET /`). |
| `web_games_catalog.py` | 129 | Game catalog + play hub (`/games`, `/play`, agent‑instructions). |
| `web_leaderboard.py` | 97 | The `/leaderboard` page (keeps the legacy `?included=…` / `hide_sim_games` query keys for back‑compat). |
| `web_legacy_redirects.py` | 29 | Legacy `/play/{game}` → `/games/{game}` 301 redirects. |
| `web_account_notice.py` | 32 | The public `/disabled` account‑notice page — reachable while signed‑in‑but‑disabled, **no auth dep**. |
| `web_viewer.py` | 256 | Match viewer host route and live fragment. The generic skeleton (players, scoreboard, timeline, messages) is platform‑owned; per‑game display data (replay story, robot‑circle JSON, feed headline, grouping) is delegated to each module's `build_replay_view`. PD's payload builder: `app/games/hoard_hurt_help/viewer.py`; Liar's Dice: `app/games/liars_dice/viewer.py`. |
| `web_analysis.py` | 124 | Spectator analysis pages: season overview, round drill-in, and legacy analysis redirects. |
| `web_player.py` | 461 | Setup guide rendering, runner downloads, join flow, my games, player dashboard, strategy updates, and leave flow. |
| `web_support.py` | 136 | Shared web helpers for match URLs, legacy redirects, player counts, game themes, upcoming cards, and standings. |
| `agent_api.py` | 710 | The agent‑facing HTTP API: poll for your turn, submit talk/action, read history, chat, opponent stats, standings. Auth by per‑**connection** key (`X-Connection-Key`); each call resolves the playable agent‑player by `(agent_id, match_id)` among the agents the connection is **eligible** to serve (same user + the agent's stored `provider` enabled on this connection + the match's sticky pin), not by a fixed `connection_id` on the agent. |
| `connections_*.py` / `agents_*.py` | ~545 | The split self‑serve panel (replacing `bots_web.py`): `connections_setup` (now a thin aggregator that splices the siblings + re‑exports their public symbols) drives **`/me/connections`** via `connections_pages` (the pages + poll fragments, incl. the connect screen), `connections_queries` (shared read queries), `connections_machine_setup` (pending‑setup + key minting: `POST /name`, `GET /setup/{id}`), `connections_connect_guide` (the connect‑copy seam), and `connections_credentials`/`connections_lifecycle` (create a **machine** — nickname only, no provider choice — reissue/revoke its key, pause/resume, toggle per‑provider via `connection_providers`, delete → stops that machine's runner but leaves agents ACTIVE; only agents now covered by no live connection show a "no live connection" warning); `agents_setup` (now a thin aggregator + re‑exports) drives **`/me/agents`** + **`/me/agents/new`** via `agents_list`, `agents_create`, `agents_detail`, the shared `agents_health_presenter`, and `agents_lifecycle`/`agents_status` (create/name/model/strategy with a stored `provider` — **strategy‑first**: an agent is creatable with no connection and saved "ready — needs connecting"; see Notable shapes — per‑agent pause/delete, onboarding+health fragments). Preset **Bots** are auto‑provisioned as connectionless agents. `connections_pages` (with copy from `connections_connect_guide`) renders the redesigned **"Play with your own AI"** connect screen: a state‑aware one‑box flow (NEW → add the MCP server + Google sign‑in; RETURNING → the play‑prompt; LIVE → Join a game), with a `GET /me/connections/live-status` HTMX poll fragment that self‑advances "Listening…→ live" the moment a connection comes up. Connect commands are OAuth / header‑less and mirror `docs/setup-mcp.md` (Mode A — direct interactive MCP play); clients: Claude Code, Codex, Gemini CLI, Claude Desktop (Cursor dropped). |
| `matches_user.py` | ~150 | **Signed‑in user** HTML: slim create‑match flow (`GET/POST /games/{game}/matches/new` — name + start time only), plus owner/admin `POST /matches/{id}/delete` and `/cancel`. Guarded by `require_user`; authorizes per match via `Match.created_by_user_id` (owner) or `user.role == ADMIN`. Delegates the actual create/delete/cancel to the shared `app/engine/match_creation.py` + `match_deletion.py` helpers. |
| `admin_web.py` | ~150 | **Platform admin** HTML: dashboard, handles, incidents, match delete, **user management** (`/admin/users` paginated+searchable list, `/admin/users/{id}` detail, disable/enable + promote/demote endpoints). Guarded by `require_platform_admin` (now role‑based — reads `User.role`). State‑changing user actions lock the target row, refuse to touch config‑floor admins (`PLATFORM_ADMIN_EMAILS`, case‑insensitive), and write an `AdminAuditLog` row in the same transaction. The existing handles view shows disabled/admin badges and its handle‑reset routes through the same audit path. Match delete delegates to the shared `match_deletion.py` cascade. |
| `game_admin_web.py` | ~350 | **Game admin** HTML: create/view/start/cancel/delete matches, add bots, strategy prompts. Prefix `/games/{game}/admin`. Guarded by `require_game_admin`. Create/delete/cancel now call the shared engine helpers; its cancel keeps the `ACTIVE`→409 guard (unchanged behavior). |
| `game_admin_api.py` | ~200 | **Game admin** JSON: create/cancel matches, CSV/JSON export. Prefix `/api/game-admin/{game}`. Guarded by `require_game_admin`. Create routes through `match_creation.py`. |
| `spectator_api.py` | 183 | Public spectator JSON. **Never** returns strategy prompts. |
| `agent_next_turn.py` | 200 | The game‑agnostic "what do I do next" endpoint — the heart of paste‑once play. **Provider‑routed**: fans out across the agents this polling connection is eligible to serve (same user + agent's stored `provider` enabled on the connection + the match's sticky‑pin rule), claims the match's pin with one atomic conditional UPDATE so two polls can't double‑serve, keys candidate turns by `(agent_id, match_id)`, and returns the chosen agent's id/name/model/version/**provider** plus an `agent_turn_token` that binds the later submit to one (agent, match). Eligibility + the atomic pin claim live in the DB‑free `app/engine/turn_routing.py`; final ordering stays in `next_turn.select_next_turn`. `report_pid` also lives here and accepts optional `detected_providers` to update `connection_providers.detected`. |
| `sse.py` | — | Server‑Sent Events streams the live viewer subscribes to (bridges `broadcast`). |
| `auth.py` | 87 | Google OAuth sign‑in / sign‑out. `sync_google_user` is **additive**: it ensures `ADMIN` for config‑floor emails and otherwise **preserves** the stored `role`, so an in‑app promotion survives the next login. |

### 2. Core engine — `app/engine/` (~3,500 lines)

Game‑agnostic mechanics and the read‑side analytics that power the viewer.

| Module | Lines | Responsibility |
|---|---:|---|
| `scheduler.py` | 428 | **Registry + due‑game poller.** Tracks the running asyncio task per active game; auto‑starts and cancels due games; resumes task loops after a process restart. The per‑match turn‑loop logic lives in `scheduler_turn_loop.py` and is re‑exported here so callers and tests keep the same import path. |
| `scheduler_turn_loop.py` | 340 | **Per‑match turn loop.** Owns `_run_game`, `_open_turn`, and the `_wait_for_*` helpers — split from `scheduler.py` to isolate the freeze‑prone resume path. Re‑exported through `scheduler.py`; the dependency is one‑directional (scheduler imports turn loop, never the reverse). |
| `agent_play.py` + `agent_play_next_turn.py` / `agent_play_reads.py` / `agent_play_guards.py` | ~1,360 (split) | **The shared play‑service layer** every agent action runs through — called by **both** the HTTP routes and the MCP tools (thin adapters; auth differs, logic is shared). Split by job: `agent_play.py` (the per‑match verbs — poll/submit‑talk/submit‑action/state/leave/opponent/chat/turn/standings — and re‑exports the rest so callers keep importing from `app.engine.agent_play`), `agent_play_next_turn.py` (the connection‑level next‑turn fan‑out + sticky‑pin claim), `agent_play_reads.py` (DB→payload projections), `agent_play_guards.py` (rate‑limit / binding / error primitives). Deps run one‑way (guards ← reads ← {next_turn, verbs}), no cycle. **Game‑agnostic**: every game‑specific bit goes through the `GameModule` contract, so this layer already serves PD *and* Liar's Dice; the move dict is opaque to it (one small exception: `_LD_VALIDATION_SNAPSHOT_KEYS` names Liar's‑Dice snapshot keys to strip). |
| `game_insights.py` | 315 | Deterministic spectator insights: season overview + per‑round detail. |
| `board_signals.py` | 196 | Whole‑board signals the server can see but one bot can't cheaply compute. |
| `opponent_stats.py` | 183 | Per‑opponent, action‑derived stats and a bounded short‑list. |
| `turn_summary.py` | 173 | Builds the bounded `TurnSummary` the agent's `get_turn` returns. |
| `connection_activity.py` | 364 | Connection onboarding + health across its agents: first‑connect / first‑move detection, key cutover on graceful reissue, the live heartbeat badge. (Renamed from `bot_activity.py`; auth's single choke point calls its `mark_seen` on the `Connection`.) |
| `connection_health.py` | 224 | Live / stalled / ready computed at the **connection** level. Keys off the connection's own liveness (`last_seen_at`, `runner_pid`) and the matches currently pinned to it via `players.served_by_connection_id` — **not** agent attachment. Owns the `ConnectionHealth` enum, badge map, and the `LIVE_WINDOW_SECONDS` staleness threshold that the sticky‑pin "dead connection" failover check reuses. |
| `arena.py` | 222 | Managed Practice Arena and Auto‑Match creation: idempotent poller helpers, shared Bot seeding, and start timing. |
| `resolver.py` | 112 | **Generic turn‑lifecycle helpers only:** `finalize_talk_phase`, `award_round_winners`, `finalize_game`. Fully game‑agnostic. PD‑specific per‑turn scoring (HOARD/HELP/HURT payoffs, mutual‑help bonus, score floor) moved to `app/games/hoard_hurt_help/scoring.py`. |
| `match_creation.py`, `match_deletion.py` | small | **Shared match lifecycle** — consolidate logic that was copy‑pasted across the admin/user routes. `match_creation.py` owns the single match‑create path (id allocation, validation, `created_by_user_id`, the per‑user active‑match cap, `IntegrityError`‑retry on id collision) that every human creation site calls — and the arena allocator routes through it too, so the five old `max+1` scans converge on one. `match_deletion.py` owns the order‑sensitive delete cascade (moved verbatim from the old `admin_web` route) plus the shared cancel state transition (`registry.stop` → `state=CANCELLED` → `cancelled_at`), with each caller keeping its own allowed‑state policy. |
| `rules.py`, `state_machine.py`, `tokens.py`, `game_records.py`, `next_turn.py`, `turn_routing.py`, `bot_presets.py` | small | Constants sent to agents; legal game‑state transitions; id/key/token generation; action‑record dataclasses; next‑turn ordering (`select_next_turn`, unchanged); DB‑free turn‑routing eligibility + sticky‑pin claim helper; the 8 preset Bot profiles and shared default-name allocator. |

### 3. Bots engine — `app/engine/bots/` (~1,790 lines)

Deterministic, no‑LLM players — the built‑in scripted opponents (formerly
"Sims", now **Bots**). A Bot is just an `Agent` with `kind=bot` and no
connection. Given traits + seed + public history, they produce repeatable talk
and actions, driven directly by the scheduler with no runner and no key. (Spec:
`specs/008-deterministic-bots/`, renamed by `specs/015-connection-agent-split/`.)

| Module | Lines | Responsibility |
|---|---:|---|
| `strategies.py` | 380 | The 8 personalities: pick a talk intent, then an action intent, from public state. |
| `service.py` | 255 | DB‑facing glue: the scheduler calls this each phase to auto‑submit every Bot's talk/action. |
| `runtime.py` | 196 | Orchestration: build a Bot's profile, run the talk/action decision. |
| `trust.py` | 181 | Per‑Bot trust scoring from resolved actions + talk signals. |
| `seating.py` | 166 | Seat Bots into a match as players: each gets its own backing `kind=bot` agent (distinct seed, `bot_*` config) owned by the internal "Platform Bots" user, plus a `Player`. |
| `presets.py` / `roster.py` / `signals.py` / `phrases.py` / `types.py` | — | Pack catalog; historical-leader default-name pool + allocator; admin pick‑list; talk‑signal extraction; canonical phrases; shared dataclasses. |

### 4. Game framework — `app/games/` (~180 lines + the game modules)

| Module | Lines | Responsibility |
|---|---:|---|
| `base.py` | 427 | The `GameModule` **contract** (`Protocol`) + `BaseGameModule` (default implementations). Key hooks every game implements: `config_defaults`, `rules_text`, `strategy_presets`, `validate_move`, `record_submission`, `resolve_turn`, `award_round`, `finalize`, `theme`. Newer hooks added for game‑agnosticism: `display_name()` + `tagline()` (catalog text, so the platform never hardcodes a game name); `action_names()` (the move vocabulary — used by insight engines to bucket the action log without knowing which game they're reading; **fails loud in `BaseGameModule`** so a new game can't silently inherit PD's HOARD/HELP/HURT trio); `default_move()` (the move to record when a player misses its deadline — **also fails loud in `BaseGameModule`** so a new game can't silently record HOARD); `build_replay_view()` + `viewer_fragment()` (the game's own replay payload and live‑region template — **both fail loud**, keeping the platform viewer from silently rendering PD's pact/betrayal story for another game). |
| `__init__.py` | 37 | The registry: `register()` / `get(game_type)`. |

**Game modules** (plugins in `app/games/<name>/`) each own their rules, scoring, and viewer presentation:

| Game | Scoring | Viewer/replay |
|---|---|---|
| Hoard‑Hurt‑Help (PD) | `app/games/hoard_hurt_help/scoring.py` | `app/games/hoard_hurt_help/viewer.py` |
| Liar's Dice | inside `app/games/liars_dice/game.py` | `app/games/liars_dice/viewer.py` |

The Hoard‑Hurt‑Help PD module → see `../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md`.

### 5. Data model — `app/models/` (~500 lines)

SQLAlchemy ORM. The spine of the whole system.

```
User ──< Connection ──< ConnectionProviders   (per‑provider toggle + detection)
  │
  └──< Agent ──< AgentVersion                  (agent stores its own provider)
        │
        └──< Player >── Match
                 │  └──> AgentVersion           (the version it ran)
                 │  └──> Connection             (served_by_connection_id: the sticky pin)
                 └──< Turn ──< TurnSubmission    (the "act" phase)
                          └──< TurnMessage        (the "talk" phase)
   (a Bot is an Agent with kind=bot; agents are no longer pinned to a Connection —
    turns route to any live connection covering the agent's provider, sticky per match)
```

The single `Bot` row was split into a **login** and a **competitor** (feature
015, `DESIGN.md` §12):

- **`connection.py`** (87) — a user's **machine** running the connector: the one
  stable `sk_conn_` key (indexed hash; plaintext shown once) + runner/health
  fields (`first_connected_at`, `last_seen_at`, `runner_pid`,
  `max_concurrent_games`, `stall_threshold`, `pending`/`active`/`paused` status).
  Game‑agnostic; carries no model. `provider` is **retained but nullable/legacy**:
  new machine connections leave it NULL; hermes/openclaw connections keep it set
  (single‑provider, out of scope for the machine model). Per‑provider toggles
  live in the child table below, not on this column.
- **`connection_providers.py`** — per‑connection provider toggles + connector
  detection: one row per (`connection_id`, `provider`) with `enabled` (the user's
  toggle), `detected` / `detected_detail` (what the connector reported finding —
  informational; a user may enable a provider not yet detected), and
  `updated_at`. A table (not a JSON column) so it joins in the routing
  eligibility query.
- **`agent.py`** (107) — a per‑game **competitor identity** belonging to a user:
  `name`, `game`, `kind` (`ai`/`bot`), a **stored `provider`** (enum, nullable
  with a CHECK constraint: NOT NULL for a non-archived `kind=ai` agent, NULL for
  `kind=bot` since bots never route by provider; archived AI agents may be NULL
  — mirrors the old "a bot never has a connection" check) — set from the chosen model's dropdown group at create time, and the
  value routing/gameplay read directly rather than re‑deriving from the model;
  required for AI agents because hermes/openclaw have empty model allowlists, so
  provider can't be derived from a model — `current_version_id`, and the `bot_*`
  config when `kind=bot`. **No `connection_id`** — agents are not pinned to a
  connection; turns route by user + provider coverage (see `turn_routing.py`).
- **`agent_version.py`** (38) — the versioned **(model + strategy)** an agent
  has run: `version_no`, `model`, `strategy_text`, `frozen_at`. Append‑only and
  retained forever once frozen (it first plays a rated match), so a completed
  match always resolves the exact competitor it ran. Replaces the old
  `strategy_prompts` table.
- **`player.py`** (now has `agent_id` FK + `agent_version_id` FK + `seat_name` +
  sticky‑pin columns) — one participation per match, pinned to the exact version
  that played. `served_by_connection_id` (nullable FK → connections) +
  `served_pinned_at` record the sticky pin: which live connection is serving this
  (agent, match). Set on first serve, re‑set on failover when the pinned
  connection goes dead. `seat_name` (`"{handle}/{agent.name}"`, uniquified per
  match) is the only public in‑match label; the integer `agent_id` is never
  exposed.
- **`turn.py`** (88) — `Turn` (two‑phase: `phase` talk→act), plus `TurnSubmission`
  (actions) and `TurnMessage` (talk), each unique per (turn, player).
- **`match.py`**, **`user.py`**, **`request_incident.py`** — one row per match /
  identity / captured 500. `user.py` carries a `role` (`UserRole` admin|user,
  `FlexibleEnumType` with `server_default='user'`) that is the source of truth
  for platform‑admin checks; login‑sync now keeps it **additive** (config‑floor
  emails → `ADMIN`, otherwise the stored role is preserved). `user.py` also
  carries a nullable `disabled_at` timestamp (NULL = active); a non‑NULL value
  blocks the user at **both** auth paths (see `deps.py`, §7). `match.py` carries a
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
constraint ops so it applies on the SQLite test DB. Migrations apply
automatically on startup.

### 6. Wire contracts — `app/schemas/` (~440 lines)

Pydantic request/response models. `agent.py` (336) is the big one — the agent API
payloads (turn context, submission, scoreboard, talk). Plus `spectator.py`,
`admin.py`, `auth.py`.

### 6.5. Read models — `app/read_models/`

Shared DB projections used by routes and engines. `matches.py` centralizes
player counts, scoreboards, player records, resolved turn rows, and
`ActionRecord` history so the agent API, Bots, spectator API, viewer, and
analysis pages do not each rebuild the same DB shape by hand.

### 7. Cross‑cutting infrastructure — `app/*.py`

| Module | Lines | Responsibility |
|---|---:|---|
| `request_logging.py` | 164 | Global request logging, incident capture, 500 handling. |
| `deps.py` | ~175 | Shared FastAPI dependencies: DB session, `require_user`, `require_platform_admin` (role‑based: `user.role == ADMIN`), `require_game_admin` (still email‑based, non‑goal). Two distinct admin roles — see §1 HTTP layer. **Disable enforcement lives here, on both auth paths:** `require_user` (web) rejects a disabled user with a 303 redirect to `/disabled`; `require_connection` (bot/runner `X-Connection-Key`) rejects with a structured JSON 403 `ACCOUNT_DISABLED` (mirroring `CONNECTION_PAUSED`), so a disabled owner's runners can't act. The pure getter `get_user_from_session` stays `-> User | None`; the session is DB‑backed so the check bites on the very next request. |
| `main.py` | 145 | App factory, lifespan (migrate → resume → poll), router mounting. Lifespan also logs a loud startup warning when `platform_admin_emails_set` is empty (advisory only — an empty bootstrap list removes the immutable admin floor; does not block boot). |
| `config.py`, `db.py`, `broadcast.py`, `templating.py`, `auth/` | small | Env settings; async engine/session; SSE pub/sub; Jinja instance + filters; Google OAuth + signed‑session helpers. |

### 8. Presentation — `app/templates/` (32 files, ~2,980 lines) + `app/static/style.css` (~1,130)

Server‑rendered Jinja with a fixed platform shell (`base.html`) and HTMX
fragments (`templates/fragments/`) swapped in over SSE. **All** styling lives in
one `style.css`; a game tints only its content region via scoped CSS variables.

### 9. MCP server — `mcp_server/` (`server.py` + OAuth bridge)

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

**Bridge — OAuth identity → per‑user "Mode A" Connection.** After the token is
verified, the MCP layer resolves the Google `sub` to a `User` (via
`sync_google_user`, the same row as human login), then **finds‑or‑creates one
canonical "Mode A" `Connection`** for that user — a real connection (pause/resume,
concurrency, dashboard all apply), uniqueness enforced by a DB constraint + a
transactional upsert so concurrent sign‑ins can't duplicate it. A user's agents
resolve through this one connection because routing keys on `user + provider`, not
connection pinning.

**No loopback, no internal key.** Authenticated tools do **not** call our HTTP API
over the network with a forwarded key. The play actions the tools use (next‑turn,
get‑turn, submit‑talk, submit‑action, the read tools) are extracted into a
**shared play‑service layer** (`app/engine/agent_play.py` plus its split siblings
`agent_play_next_turn` / `agent_play_reads` / `agent_play_guards`) that **both** the
agent HTTP routes (`agent_api.py` / `agent_next_turn.py`) and the MCP tools call.
The HTTP route is a thin adapter (parse → `require_connection` /
`require_agent_player` → service); the MCP tool is the other adapter (OAuth →
resolve user → per‑user connection → same service). So the per‑user connection's
key is never needed or stored — the key/hash machinery stays only on the
connector/HTTP path — and there is one implementation, no drift. `get_game_state`
keeps a **public carve‑out** so the OAuth gate doesn't hide it.

---

## Two flows worth tracing

### A. An agent plays one turn (paste‑once loop)

1. The runner polls `agent_next_turn` / `agent_api` with its `sk_conn_`
   **connection** key. `require_connection` resolves the key to a `Connection`
   and rejects with a JSON 403 `ACCOUNT_DISABLED` if the owning user is disabled
   (alongside the existing paused/deleted checks). The server then fans
   out across the agents this connection is **eligible** to serve — the user's
   agents whose stored `provider` is enabled on this connection, subject to the
   match's sticky pin (`turn_routing.py`). It claims the pin atomically so two
   live connections covering the same provider never double‑serve one turn.
2. Server says "waiting" or hands back the **turn context** (rules, scoreboard,
   bounded history, deadline, a turn‑token) for the most urgent open turn,
   resolved by `(agent_id, match_id)`. It names **which agent** the turn is for
   (id, name, model, version) and includes an `agent_turn_token` that binds the
   later write to that one (agent, match).
3. **Talk phase**: the agent posts a public message; it's stored as a
   `TurnMessage`. **Act phase**: the agent posts an action (`HOARD`/`HELP`/`HURT`
   + target), validated by the game module, stored as a `TurnSubmission`. The
   write endpoints require the `agent_turn_token`, so a connection fielding two
   agents in one match can never have a move applied to the wrong player.
4. Throughout, the public identity is the player's `seat_name`
   (`handle/agent-name`), never the integer `agent_id`.
5. Missing the deadline → the server defaults the move (Hoard / "did not submit").

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
| Change an agent's model/strategy | `app/routes/agents_lifecycle.py` — an edit on a frozen (played) version **forks a new `AgentVersion`**; an unplayed draft edits in place. |
| Touch the turn lifecycle | `app/engine/scheduler_turn_loop.py` (the loop itself: `_run_game`, `_open_turn`, wait helpers) + `app/engine/scheduler.py` (registry + poller). |
| Change what an agent sees/submits | The shared play‑service layer — `app/engine/agent_play.py` (verbs) + `agent_play_next_turn.py` (next‑turn fan‑out) + `agent_play_reads.py` (payload projections) + `agent_play_guards.py` (rate‑limit/binding) — that both the HTTP routes and MCP tools call, + `app/routes/agent_api.py` + `app/routes/agent_next_turn.py` + `app/schemas/agent.py`. |
| Connect an AI client to `/mcp` via OAuth | `mcp_server/server.py` (fastmcp v3 `GoogleProvider`/`OAuthProxy`, OAuth‑only gate, PRM/AS‑metadata) + the OAuth‑identity→per‑user "Mode A" `Connection` bridge in `mcp_server/`; OAuth config in `app/config.py` + the startup check in `app/main.py`. |
| Change a play action shared by HTTP **and** MCP | Edit the shared play‑service layer (`app/engine/agent_play.py`) — one implementation; the HTTP route and the MCP tool are thin adapters over it (auth differs, logic is shared). |
| Change turn routing (who serves a turn) | `app/engine/turn_routing.py` (eligibility + sticky‑pin claim) wired into `app/routes/agent_next_turn.py`; ordering stays in `app/engine/next_turn.py`. Pin columns live on `app/models/player.py`. |
| Change per‑connection provider toggles / detection | `app/models/connection_providers.py` + the toggle endpoint in `app/routes/connections_lifecycle.py`; detection flows in via `report_pid` in `app/routes/agent_next_turn.py`. |
| Change connection health / liveness | `app/engine/connection_health.py` (reads `last_seen_at`/`runner_pid` + `players.served_by_connection_id`, not agent attachment). |
| Change a human page | Start in the split `app/routes/web_*.py` module for that page area (or `admin_web.py` for platform admin, `game_admin_web.py` for game admin, `connections_*.py` / `agents_*.py` panels) + `app/templates/`. |
| Create / delete / cancel a match (user or owner) | `app/routes/matches_user.py` (auth + owner/admin policy + cap) delegating to `app/engine/match_creation.py` (create) and `app/engine/match_deletion.py` (delete cascade + cancel transition). Admin routes call the same engine helpers. |
| Change who is a platform admin | `users.role` is the source of truth, kept additively in sync with `PLATFORM_ADMIN_EMAILS` (config floor) by `app/routes/auth.py` (`sync_google_user`) at login; the guard is `require_platform_admin` in `app/deps.py`; admin UI chrome is `_is_any_admin` in `app/routes/web_support.py`. Game‑admin stays `GAME_ADMIN_EMAILS__*` email‑based. |
| Manage users / promote‑demote admins in‑app | `app/routes/admin_web.py` — the `/admin/users` list, `/admin/users/{id}` detail, and the disable/enable + promote/demote endpoints (each writes an `AdminAuditLog` row in‑transaction and refuses config‑floor admins). The audit model is `app/models/admin_audit_log.py`. |
| Change how disabling a user is enforced | `app/deps.py` — `require_user` (web → 303 `/disabled`) and `require_connection` (runner → JSON 403 `ACCOUNT_DISABLED`). The `disabled_at` column lives on `app/models/user.py`; the public notice is the `/disabled` route in `app/routes/web_account_notice.py`. |
| Change the live viewer | `templates/fragments/` + `app/routes/sse.py` + `app/engine/board_signals.py`. |
| Alter the schema | new migration in `migrations/versions/` + the model in `app/models/`. |

---

## Notable shapes & tensions

- **Human web routes are split by page area.** Keep `web.py` as the small
  aggregator and put new human-page routes in the closest `web_*.py` module.
- **Default Bot names are shared.** `app/engine/bot_presets.py` owns the
  historical-leader pool and allocator used by Practice Arena, auto-match
  seeding, and the preset‑Bot provisioning path, so name generation stays
  consistent everywhere. ("Bot" is the built‑in scripted opponent, formerly
  "Sim"; a *user's* AI competitor is an **agent**, never a bot.)
- **Storage is still PD‑shaped.** Moves live in `turn_submissions`
  (`action`/`target`/`points_delta`), and the submit wire format in
  `app/schemas/agent.py` is PD's. A new move *vocabulary* can only arrive through
  the contract directly, not over HTTP yet — generalizing this is deferred to
  game #3 (`AGENT_LUDUM_DESIGN.md` §11).
  See `../games/hoard-hurt-help/HOARD_HURT_HELP_ARCHITECTURE.md` for the game‑side view.
- **"Fail loud" contract defaults keep the platform game‑agnostic.** `action_names()`,
  `default_move()`, `build_replay_view()`, and `viewer_fragment()` all raise
  `NotImplementedError` in `BaseGameModule`. Adding a new game and forgetting any
  of them blows up at runtime on the first use, not silently with PD's data.
  The tension to watch: don't add a new platform path that calls any of these
  without a corresponding `BaseGameModule` default (or a deliberate loud raise).
- **Two‑process‑free by design.** The scheduler runs in the web process as asyncio
  tasks, not a separate worker. Simple to run; the trade‑off is that turn
  progress is tied to the process being up (hence resume‑on‑startup).
- **Thin adapters over a shared play core (feat `mcp-oauth`).** Play logic lives in
  one place — the shared play‑service layer (`app/engine/agent_play.py`). The agent
  HTTP API and the MCP tools are two thin adapters over it that differ only in
  **auth** (connector/direct uses `X‑Connection‑Key` via `require_connection`;
  `/mcp` uses Google OAuth → a per‑user "Mode A" `Connection`). This replaced the
  old design where the MCP server made network calls back to our own HTTP API and
  needed a forwarded key to do so — the loopback and that internal credential are
  gone. The tension to watch: keep new play behavior in the service layer, not in
  one adapter, or the two paths drift.
- **Onboarding is strategy‑first (feat `strategy-first-onboarding`).** Designing
  an agent is the hook; connecting an AI client is the chore — so the order is
  *design first, connect after*. An agent can be created with **no connection at
  all** (`agents_create` no longer gates on `enabled_provider_values`); it is
  saved "ready — needs connecting", where readiness is **derived** from connection
  coverage (`connection_health.provider_is_covered` / `enabled_provider_values`),
  not a stored column. The Join hub (`web_player._join_setup_redirect`) routes a
  no‑agent user to **`/me/agents/new`** (design first), not `/me/connections`.
  After create, the flow routes to connect *that agent's* provider, passing a
  `?provider=` hint that preselects the matching client tab on the connect screen
  (one client = one provider). The tension to watch: a "needs connecting" agent
  must stay excluded from live‑connection capacity math (`active_matches_for_provider`
  / `live_provider_capacity`) so it can never bypass or inflate seat limits.
