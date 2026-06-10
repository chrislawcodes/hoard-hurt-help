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
- **RETAIN `provider` as a nullable "legacy connection type" this run — do NOT
  drop it.** Routing for claude/gemini/openai stops reading it (it reads
  `connection_providers` instead), but several paths still need a connection's
  legacy identity and would break if it vanished: setup-script selection and
  the provider label (`connections_setup.py`), and reattach validation
  (`connections_lifecycle.py`). Hermes/OpenClaw connections — which are
  inherently single-provider and out of scope for the machine model — keep
  `provider` set and behave exactly as today. New machine-style connections
  created after this change leave `provider` NULL. The column's eventual drop
  belongs to the follow-up adapter run (keep-then-drop, see §6), not here. Make
  the column `nullable=True`.
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
  only.
- ADD an explicit `provider` column (enum, NOT NULL) to the agent. The agent's
  provider is **stored**, not derived from the model name. This is required:
  Hermes/OpenClaw use empty `PROVIDER_MODELS` allowlists (empty list = "any
  model" today — `app/config.py:160`), so there is no model list to reverse-map
  from. A pure model→provider derivation would break those providers, which the
  discovery decision says must keep working unchanged. Backfill `provider` from
  the connection's provider during migration (see §6).
  - During agent **creation** for claude/gemini/openai, the chosen model comes
    from the grouped dropdown, so the provider is set from that model's group.
    A startup assertion in `app/config.py` enforces that model names are unique
    across the three non-empty-allowlist providers, so the dropdown's
    model→group mapping is unambiguous. The assertion skips empty allowlists
    (hermes/openclaw).
  - Routing and all gameplay paths read the stored `agent.provider`, never a
    re-derived value.
- Agent "needs a connection" UI state is replaced by a computed
  "no live connection covers this provider" warning.

#### `app/models/player.py` (sticky pin)
- ADD `served_by_connection_id` (nullable FK → connections.id) and
  `served_pinned_at` (nullable datetime) to the player row (a player is one
  agent in one match). Set when a connection is first handed a turn for that
  (agent, match). Cleared/re-set on failover.

### 2. Turn routing — `app/routes/agent_next_turn.py`

Routing stays pull-based: the connector polls, the server picks. The candidate
query changes from `Agent.connection_id == connection.id` to:

- agent belongs to the same user as the polling connection, AND
- the agent's stored `provider` (the new `agents.provider` column) is `enabled`
  on the polling connection, AND
- sticky rule: the player's `served_by_connection_id` is NULL, OR equals the
  polling connection, OR the pinned connection is **dead** (paused, deleted,
  or `last_seen_at` stale per `app/engine/connection_health.py` thresholds).

When a turn is served, set the pin to the polling connection. The pin moving
on failover is the only write path; no background job is needed.

**Race-safe pin write (no double-serving).** The next-turn flow is read-only
today; adding "set the pin" introduces a write two concurrent polls could race,
double-serving one turn to two machines. The pin claim MUST be a single atomic
conditional UPDATE — set `served_by_connection_id = :me, served_pinned_at = now`
WHERE the player row still satisfies the sticky rule (pin is NULL, already me,
or the pinned connection is dead) — and the poll only proceeds to serve the
turn if that UPDATE reports it changed the row (i.e. this poll won the claim).
A poll that loses the conditional write gets "no turn for you right now" and
re-polls. This makes concurrent claims safe without a background job or table
lock. Cover it with a test that simulates two simultaneous claims and asserts
exactly one wins.

**Shared resolution helpers must move off `connection_id` too.** The candidate
query is not the only place that reads `Agent.connection_id`. These three
helpers gate live gameplay and the join/nav UI and MUST be redesigned in this
run, or detached agents stop resolving for turns and vanish from the UI:
- `app/deps.py::require_agent_player` filters active seats with
  `Agent.connection_id == connection.id` (`app/deps.py:241`). It must resolve
  the connection's playable seats by **user ownership + the agent's provider
  being enabled on this connection** instead.
- `app/routes/nav_context.py::user_has_connected_agent` joins
  `Agent → Connection` on `connection_id` (`app/routes/nav_context.py:41`). It
  must become "user owns an AI agent whose provider is covered by a connection
  that has connected at least once."
- `app/routes/web_player.py` join/display/capacity paths (see §4) read agents
  through `connection_id`; they move to the stored-provider + enabled-providers
  model.
- `app/engine/connection_health.py` decides a connection's
  READY/LIVE/STALLED/DISCONNECTED status by querying `Agent.connection_id` and
  checking players through that attachment. After detachment this misreports
  the health badge and reconnect logic. Rewrite it around **provider coverage +
  the sticky pin**: a connection's health reflects its own liveness
  (`last_seen_at`, PID) and the matches currently pinned to it
  (`players.served_by_connection_id`), not agent attachment. Its staleness
  thresholds (still used by the §2 sticky "dead" check) are unchanged.

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
  keep prefix inference and `--provider` override as fallbacks. The connector
  still uses the turn's `model` field exactly as today — the new `provider`
  field only selects which adapter handles the turn, it does not change model
  selection.
- DO NOT delete `scripts/agentludum_setup_hermes.py` or
  `scripts/agentludum_setup_openclaw.py` in this run. Those wrappers and the
  `_SETUP_SCRIPTS` / `_AGENT_RUNNERS` download aliases that serve them
  (`app/routes/connections_setup.py:56`, `app/routes/web_player.py:95`) keep
  hermes/openclaw connections working, which discovery says must stay
  unchanged. Deleting them would 404 old setup links and break that guarantee.
  Their removal belongs to the follow-up Hermes/OpenClaw adapter run, not here.
  For claude/gemini/openai, `_SETUP_SCRIPTS` already maps all three to the one
  connector — no change needed there.

### 4. Routes

#### `app/routes/connections_setup.py`
- Connection creation takes only an optional nickname — no provider choice,
  no provider groups (`_PROVIDER_GROUPS` removed). New connections start with
  all providers toggled OFF; first detection report can pre-toggle nothing
  (user enables explicitly).
- **Pending-setup flow (`ConnectionSetup`, `app/models/connection_setup.py`).**
  `ConnectionSetup.provider` is required today; `_load_resumeable_pending_setup`
  resumes by `(user_id, provider)` and `create_connection` reuses that row.
  Make `provider` nullable. For machine-style setups it is `NULL`, so the
  resume lookup becomes `(user_id, provider IS NULL)` returning the user's
  single most-recent non-expired machine draft. Collapsing all machine drafts
  into one NULL bucket is acceptable — a user sets up one machine at a time, so
  "resume my in-progress machine setup" is the intended behavior (not
  per-draft keying). Existing provider-scoped pending setups (and any
  hermes/openclaw setup) keep their `provider` value and resume exactly as
  today via the unchanged `(user_id, provider)` path. Do not drop the column
  this run.
- Detail page gains the Providers box (toggles + detection status) and a
  Recent activity box (last N turns served by this connection, from the
  sticky-pin + submission records).
- ADD toggle endpoint: `POST /me/connections/{id}/providers/{provider}`
  (enable/disable). Disabling a provider that strands agents (no other live
  connection covers it) requires a confirm step (same pattern as delete
  confirm in `app/routes/connections_lifecycle.py`).

#### `app/routes/connections_lifecycle.py`
- Reattach flow (`/reattach`, lines ~133-143) is deleted — there is nothing to
  reattach to. Pause/resume/delete stay.
- **Delete behavior must change, not just its copy.** Today
  `delete_connection()` sets `Agent.connection_id = None` and
  `Agent.status = PAUSED` for *every* attached AI agent. Under the new model an
  agent is no longer attached to a connection, so deleting a machine must NOT
  blanket-pause agents: an agent keeps playing if any *other* live connection
  still covers its provider. Delete therefore stops the runner/PID for that
  machine and removes its `connection_providers` rows, but leaves agents
  ACTIVE; only agents whose provider is now covered by no live connection
  surface the "waiting / no live connection" warning. The same coverage check
  drives the confirm step (warn before delete if it would strand agents).
- Pause/resume operate on the machine (and its provider coverage), not on
  agent attachment.

#### `app/routes/agents_setup.py`
- Create form: name, model (grouped, availability-aware), strategy. Provider
  and connection params removed from the POST. The provider is set from the
  chosen model's dropdown group and **stored** on `agents.provider`; it is not
  re-derived later. Replaces `PROVIDER_MODELS.get(connection.provider...)`
  checks.
- The combined "connect a new AI inline" flow survives but creates a
  machine-style connection (no provider).

#### `app/routes/nav_context.py`
- `user_has_connected_agent` and any other `Agent → Connection` joins on
  `connection_id` move to the stored-provider + connection-coverage model
  (see §2). Drives the Play-button CTA label, so it must not regress.

#### `app/routes/agents_lifecycle.py`, `app/routes/web_player.py`
- All `connection.provider` / `PROVIDER_MODELS.get(connection.provider...)` /
  `Agent.connection_id` reads replaced with the stored-`agent.provider` +
  enabled-providers checks.
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
  whichever live connection covers their provider."
- Nav CTA + lobby copy: the Play-button label (driven by
  `nav_context.user_has_connected_agent`, §4) and any lobby/onboarding copy
  that describes "one connection per provider" must be updated to the
  machine-level model so the UI stops describing the old flow after the backend
  changes. Audit `app/templates/` for "connection" copy tied to provider
  choice.
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
2. Add `agents.provider` (NOT NULL after backfill); backfill each agent's
   provider with this **explicit precedence** (no guessing):
   a. If the agent is still attached (`connection_id` is set) → use that
      connection's `connections.provider`.
   b. Else (detached, `connection_id IS NULL`) and the agent has a
      `current_version_id` → reverse-map that version's model through
      `PROVIDER_MODELS` (unambiguous for claude/gemini/openai because of the §1
      uniqueness assertion).
   c. Else (detached AND [no current version, OR model maps to no provider —
      e.g. a black-box hermes/openclaw agent whose connection is already
      gone]) → the migration **fails loudly** and lists the offending agent
      ids. `current_version_id` is nullable in today's schema, so a
      null-version detached agent lands here too. Do not default or guess. This
      is expected to be empty or tiny; if it fires, resolve those rows by hand
      before re-running. The `--dry-run` pass surfaces this list before any
      live run.
   (This column is what makes Hermes/OpenClaw agents survive — their provider
   can't be derived from a model.)
3. Add `players.served_by_connection_id` + `served_pinned_at`; backfill from
   the player's agent's current `connection_id` for active matches (so sticky
   routing starts already-pinned and no session context is lost).
4. Drop `agents.connection_id` in this release once all routing/auth/UI paths
   read the new tables. **Keep `connections.provider` (now nullable, see §1) —
   it is NOT dropped this run** because setup-script selection, labels, and
   hermes/openclaw identity still read it. Its drop is deferred to the
   follow-up adapter run. The spec's default: keep-then-drop (two migrations) —
   safer for a rolling deploy.
5. Detached paused agents (`connection_id IS NULL` today) need no special
   routing handling beyond step 2: after migration they are simply agents whose
   stored provider may or may not be covered by a live connection; the resume
   path checks coverage instead of attachment.

**Rollout-race handling.** The backfill of pins (step 3) and provider toggles
(step 1) runs inside the migration, before the new routing code that reads
those columns is serving. With the keep-then-drop ordering, release 1 adds and
backfills the new columns while routing still reads `connection_id`; release 2
flips routing to the new columns only after the backfill is committed
everywhere. So no live turn races a half-written pin. Per-row SQLite-batch
constraint changes follow the repo's `op.batch_alter_table` rule.
   verification: run the migration against a seeded copy of a production-shaped
   DB (per the data-critical-waves rule), then assert: every connection has
   exactly one enabled `connection_providers` row matching its old provider;
   every agent's `provider` equals its old connection's provider; every active
   match's players are pinned to the connection that would have served them
   pre-migration; row counts for connections/agents/players are unchanged. A
   `--dry-run` pass is reviewed before any live run.

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
   engine tests), toggle endpoint validation, the dropdown's model→provider
   group mapping + the `app/config.py` uniqueness assertion, report-pid with
   and without `detected_providers`, and the join-gate sum.
6. Migration on a copy of a production-shaped DB: every existing connection
   ends with exactly its legacy provider enabled; every agent's stored
   `provider` equals its old connection's provider (including hermes/openclaw);
   every active match's players are pre-pinned; no agent loses its version
   history; a turn served immediately after migration routes to the same
   connection it would have before.
7. An old connector (no `detected_providers`, no payload-`provider` reading)
   still plays correctly against the new server.
8. A hermes or openclaw connection created before this change still loads its
   detail/setup page and still serves its agents' turns after migration (its
   setup script and download alias are untouched).

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
- **No live connection covers an agent's provider** (every machine paused, or
  the provider toggled off everywhere). The agent must simply **wait** — its
  turns go unserved and the UI shows the "no live connection runs <provider>"
  warning — not crash, error the match, or default the turn. verification:
  unit-test the eligibility helper returns "no candidate" (not an exception)
  when zero connections cover the provider, and confirm the match stays
  resumable once a covering connection comes back.
