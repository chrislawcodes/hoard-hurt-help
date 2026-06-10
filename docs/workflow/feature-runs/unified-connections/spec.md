# Spec: Unified Connections — one connector per machine, dynamic turn routing

**Slug:** unified-connections
**Branch:** claude/awesome-mendel-y2hj7c (spec authored remotely; checkpoints run locally)

## Background

Today a connection is one AI-provider login: `Connection.provider` is fixed at
create time (`app/models/connection.py:37`), agents are pinned to one
connection (`Agent.connection_id`), and every model check goes through
`PROVIDER_MODELS[connection.provider]`. A user who wants agents on Claude,
Gemini, and OpenAI must create three connections, pick a provider three times,
and run three connector processes — even though `scripts/agentludum_connector.py`
is already one script that drives all three CLIs by model-name prefix.

This feature flips the model: a **connection = one computer running the
connector**. Install once per machine, get one key. Providers are toggled
on/off per connection from the site. Agents stop being pinned to a connection
entirely — an agent is a name + model + strategy, and each turn is served by
whichever live connection covers the agent's provider (sticky per match, with
failover).

Hermes/OpenClaw connector adapters are explicitly **out of scope** (follow-up
run). This run covers claude/gemini/openai end to end; hermes/openclaw
connections keep working exactly as well as they do today (their enum values
and free-model behavior are preserved through the migration).

## Design decisions already made (discovery)

1. Agent creation = name + model + strategy. No machine/provider step. The
   model dropdown groups models by provider and disables providers not enabled
   on any of the user's connections (greyed out, with a pointer to
   `/me/connections`).
2. Two machines with the same provider form a pool: sticky-per-match routing
   with failover. Failover is safe because the connector re-establishes a
   session with the FULL game history on first contact with an unknown match
   (`_setup_body` in `scripts/agentludum_connector.py`).
3. The name "Connections" stays. Nickname is the machine name ("Home Mac").
4. Hermes/OpenClaw agents will use whatever model their own tool is configured
   with — relevant here only in that nothing in this run may hard-require a
   provider→models list to be non-empty.

## Changes

### 1. Data model

#### `app/models/connection.py`
- REMOVE `provider` column (after migration; see §6).
- ADD `enabled_providers` — per-connection provider toggles. New table
  `connection_providers` (connection_id FK, provider enum, enabled bool,
  detected bool, detected_detail str nullable, updated_at) rather than a JSON
  column, so toggles are queryable in the routing join.
  - `enabled` = user's choice (the toggle).
  - `detected` / `detected_detail` = what the connector reported finding
    installed ("CLI detected · signed in" / "not found"). Informational only;
    a user may enable a provider the connector has not (yet) detected.
- `max_concurrent_games`, `stall_threshold`, `runner_pid`, key columns, and
  status lifecycle stay on the connection unchanged.

#### `app/models/agent.py`
- REMOVE `connection_id` (after migration; see §6). An agent belongs to a user
  only. The agent's provider is derived from its current version's model via
  reverse `PROVIDER_MODELS` lookup (model names are unique across providers —
  add a startup assertion for this in `app/config.py`).
- Agent "needs a connection" UI state is replaced by a computed
  "no live connection covers this model" warning.

#### `app/models/player.py` (sticky pin)
- ADD `served_by_connection_id` (nullable FK → connections.id) and
  `served_pinned_at` (nullable datetime) to the player row (a player is one
  agent in one match). Set when a connection is first handed a turn for that
  (agent, match). Cleared/re-set on failover.

### 2. Turn routing — `app/routes/agent_next_turn.py`

Routing stays pull-based: the connector polls, the server picks. The candidate
query changes from `Agent.connection_id == connection.id` to:

- agent belongs to the same user as the polling connection, AND
- the agent's provider is `enabled` on the polling connection, AND
- sticky rule: the player's `served_by_connection_id` is NULL, OR equals the
  polling connection, OR the pinned connection is **dead** (paused, deleted,
  or `last_seen_at` stale per `app/engine/connection_health.py` thresholds).

When a turn is served, set the pin to the polling connection. The pin moving
on failover is the only write path; no background job is needed.

The turn payload ADDS an explicit `"provider"` field (string). The connector
must prefer it over model-prefix guessing (`_provider_from_model` stays as a
fallback for old payloads).

Selection priority (`app/engine/next_turn.py::select_next_turn`) is unchanged.
New tests in `app/engine/` for the eligibility/sticky logic (extract it into a
DB-free helper, e.g. `app/engine/turn_routing.py`, so it is unit-testable per
CLAUDE.md testing rules).

### 3. Connector — `scripts/agentludum_connector.py`

- On startup, detect installed CLIs (`shutil.which` for `claude`, `codex`,
  `gemini`) and report them in the existing best-effort startup call: extend
  `POST /api/agent/report-pid` body to
  `{"pid": ..., "detected_providers": ["claude", "openai"]}` (server stores
  into `connection_providers.detected`). Old connectors that send only `pid`
  must keep working (field optional).
- Per-turn provider resolution: trust the new payload `provider` field first;
  keep prefix inference and `--provider` override as fallbacks.
- DELETE `scripts/agentludum_setup_hermes.py` and
  `scripts/agentludum_setup_openclaw.py` (18-line wrappers around the same
  script); `_SETUP_SCRIPTS` in `app/routes/connections_setup.py` collapses to
  the single connector for all providers.

### 4. Routes

#### `app/routes/connections_setup.py`
- Connection creation takes only an optional nickname — no provider choice,
  no provider groups (`_PROVIDER_GROUPS` removed). New connections start with
  all providers toggled OFF; first detection report can pre-toggle nothing
  (user enables explicitly).
- Detail page gains the Providers box (toggles + detection status) and a
  Recent activity box (last N turns served by this connection, from the
  sticky-pin + submission records).
- ADD toggle endpoint: `POST /me/connections/{id}/providers/{provider}`
  (enable/disable). Disabling a provider that strands agents (no other live
  connection covers it) requires a confirm step (same pattern as delete
  confirm in `app/routes/connections_lifecycle.py`).

#### `app/routes/connections_lifecycle.py`
- Reattach flow (`/reattach`, lines ~133-143) is deleted — there is nothing to
  reattach to. Pause/resume/delete stay; delete copy changes ("agents keep
  playing if another live connection covers their provider; otherwise they
  wait").

#### `app/routes/agents_setup.py`
- Create form: name, model (grouped, availability-aware), strategy. Provider
  and connection params removed from the POST. Model→provider derivation
  replaces `PROVIDER_MODELS.get(connection.provider...)` checks.
- The combined "connect a new AI inline" flow survives but creates a
  machine-style connection (no provider).

#### `app/routes/agents_lifecycle.py`, `app/routes/web_player.py`
- All `connection.provider` / `PROVIDER_MODELS.get(connection.provider...)`
  reads replaced with the derived-provider + enabled-providers checks.
- Join gate: `active_match_count >= connection.max_concurrent_games`
  (`web_player.py:300`, `agents_setup.py:334`) becomes: user's active matches
  for agents of provider P >= SUM of `max_concurrent_games` over the user's
  live connections with P enabled. (Capacity scales with machines; the plan
  may simplify if this proves awkward.)

#### `app/routes/agent_next_turn.py::report_pid`
- Accepts optional `detected_providers`; updates `connection_providers`.

### 5. Templates & copy

- `app/templates/connections/list.html` — machine cards: nickname, health,
  PID, provider ✓/✗ summary line, "played N turns today", stranded-agents
  warning on stopped connections. Footer explainer: "Your agents run on
  whichever live connection covers their model."
- `app/templates/connections/detail.html` (and `connection.html`) — Providers
  box, Recent activity box, runner setup message (single script, no provider
  wording), settings unchanged.
- `app/templates/agents/*` — drop connection labels; agent rows show
  `model · vN`; "Needs a connection" becomes "No live connection runs
  <provider> — turn it on at /me/connections".
- `COPY.md` updated where it documents these pages.

### 6. Migration (single Alembic migration, plus a compatibility window)

Order matters; running games must not stall:

1. Create `connection_providers`; backfill one row per existing connection:
   its legacy `provider` value, `enabled=true`, `detected=false`.
2. Add `players.served_by_connection_id` + `served_pinned_at`; backfill from
   the player's agent's current `connection_id` for active matches (so sticky
   routing starts already-pinned and no session context is lost).
3. Drop `agents.connection_id` and `connections.provider` in the SAME release
   only if all code paths are already reading the new tables; otherwise keep
   the columns one release with reads removed, drop in a follow-up migration.
   The spec's default: keep-then-drop (two migrations) — safer for a rolling
   deploy.
4. Detached paused agents (`connection_id IS NULL` today) need no special
   handling: after migration they are simply agents whose provider may or may
   not be covered; the resume path checks coverage instead of attachment.

### 7. Out of scope (non-goals)

- Hermes/OpenClaw connector adapters and their "uses your own setup" model UX
  (follow-up run; blocked on a live-install spike for Hermes session-id
  capture).
- Renaming Connections → Machines.
- Any change to the direct-MCP play path (`docs/setup-mcp.md`, `mcp_server/`).
- Leaderboard/match-page presentation changes for black-box agents.
- Multi-key connector (`--key` repeatable) — superseded by this design.

## Acceptance criteria

1. Creating a connection asks for no provider; setup yields one key + the one
   connector script.
2. Connection detail shows per-provider toggles with detection status; turning
   off a provider that strands agents requires explicit confirmation.
3. Agent creation = name + grouped availability-aware model dropdown +
   strategy; submitting with a model whose provider is enabled nowhere is
   rejected with a clear message.
4. With two live connections both covering Claude, a match's turns keep going
   to the connection that served the match first; killing that connector makes
   the other one pick the match up within one health-staleness window, and the
   match completes correctly (fresh session, full history).
5. `pytest -q` covers: eligibility/sticky/failover selection logic (DB-free
   engine tests), toggle endpoint validation, model→provider derivation,
   report-pid with and without `detected_providers`, and the join-gate sum.
6. Migration on a copy of a production-shaped DB: every existing connection
   ends with exactly its legacy provider enabled; every active match's players
   are pre-pinned; no agent loses its version history; a turn served
   immediately after migration routes to the same connection it would have
   before.
7. An old connector (no `detected_providers`, no payload-`provider` reading)
   still plays correctly against the new server.

## Risks

- **Routing regression stalls live games** (hot path). Mitigation: DB-free
  engine tests for the new selection logic + acceptance test #4.
  verification: run two local connectors against a dev server, kill one
  mid-match, observe failover and match completion before merge.
- **Migration data loss / wrong pins.** verification: run the migration
  against a seeded copy of the production schema and diff
  connection/agent/player counts and pins before merge (acceptance #6).
- **Join-gate sum semantics surprise users** (capacity now scales with live
  machines). verification: unit test the gate at 0, 1, and 2 live connections;
  copy on the join-blocked message states the rule.
- **Backward compatibility with running connectors.** Old connectors poll the
  same endpoint with the same key; the key's connection keeps its (now
  toggled) provider, so turns still flow. verification: acceptance #7 run
  with the pre-change script checked out from git history.
