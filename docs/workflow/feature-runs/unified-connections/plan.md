# Plan

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: CONVERGED at spec level. All round-4 findings name specific FUNCTIONS inside files already in the spec's scope (no new systems vs round 3). Accepted and carried forward as explicit PLAN inputs (function-level enumeration is plan work, not spec work): (1) connections_setup.py needs provider-NULL branches in _provider_label(), _setup_message() [single connector script], _load_detached_agents() [PROVIDER_MODELS index], connection_setup_detail() and the detail/list routes (agent_count/health rendering); acceptance #1 and #8 already gate that these pages must not break. (2) agents_setup.py new_agent_form() must build model/provider choices from the grouped availability-aware dropdown (not Connection.provider), and edit_agent_version_page()/save_version() must validate models without an attached connection; web_player.py:148/181/243 readiness + capacity move off Agent.connection_id to the stored-provider + enabled-providers + join-gate-sum model (spec §4). (3) compute_connection_health() (connection_health.py:98/150) + connection list/detail agent_count move to the provider-coverage + sticky-pin model (spec §2 helper list). Residuals already covered: null/orphan-version migration -> §6 step-2c loud-fail (+--dry-run); atomic pin claim -> §2 race-safe conditional UPDATE + required two-claim test; template audit -> §5. The PLAN must address each named function; the plan checkpoint will re-review.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: CONVERGED. Findings 1 (atomic pin claim), 2 (provider-coverage routing replacing connection_id filter), 4 (agents.provider column) are all 'spec requires X, current code lacks X' — i.e. they confirm the implementation work this spec defines, not spec gaps. The spec already mandates each (§2 race-safe pin write, §2 candidate query + shared helpers, §1 agents.provider). Finding 3 (config.py admin-email fallback / game_admin_emails) is unrelated to unified-connections — REJECTED as out of scope. Residual 1 (null/orphaned-version migration failure) is already handled by §6 step-2c loud-fail precedence with --dry-run surfacing. Residual 2 (health badge) is the §2 connection_health rewrite, already in scope. No spec edit required.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: Round-5 (real). 2 findings + residuals, all converged to single-function attachment reads caught by the existing Slice-8 mandatory repo-wide grep sweep ('every remaining Agent.connection_id / connection.provider read ... resolve or annotate each hit'), plus per-slice diff review. Specifics carried into implementation: (1) create_connection provider field — machine-connection create drops it (nickname-only, Slice 5); the LEGACY hermes/openclaw provider-specific setup path is explicitly OUT of scope (spec §7) and stays provider-specific, so legacy connections remain provisionable via their unchanged path — no regression. (2) web_player.player_dashboard (web_player.py:427/443) joins Agent.connection_id to render agent key/version/connection — ADD to the Slice 4 web_player rewrite (route reads stored agent.provider + the covering/pinned connection instead of one attached connection). (3) first_connected_at/onboarding state: 'has this agent connected' becomes 'has any connection covering this agent's provider connected' (nav_context model, Slice 4) — the onboarding/status fragments use that, not a single attached connection. (4) Archiving unresolvable orphans vs manual cleanup: accepted trade — --dry-run still lists them for review (data-critical-waves), archive is recoverable, and avoids a deploy blocker (Railway preDeployCommand). No plan-body edit (keeps both reviews hash-current); these are implementation directives the Slice-8 sweep + diff checkpoints enforce.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: CONVERGED. (1) 'connection_health is not a stub / arch doc says ~120 planned' — STALE/HALLUCINATED: the arch doc was already corrected to 224 lines with the post-feature behavior; grep confirms no '~120 planned' string remains. (2) downgrade permanent data loss — the plan already states downgrade is structural/forward-only-for-data and does not reconstruct attachment; accepted as the explicit, documented decision. (3) dangling/invalid connection_id treated as attached — the backfill resolves 'attached' via a join to a live connection row; a connection_id that does not resolve falls through to reverse-map or archive (LEFT JOIN semantics), so it cannot corrupt provider; FK integrity makes true dangling refs unlikely. Residuals: live-but-misconfigured health is the accepted liveness-based-health limitation (idle-but-live = READY by design); toggle/delete divergence is prevented by the one-shared-coverage-helper directive (Slice 5/6); preDeployCommand runs migrations before container start (Postgres transactional DDL) so there is no serve-during-migration window. No plan edit required.
- review: reviews/diff.gemini.regression-adversarial.review.md | status: accepted | note: Slice 2 (turn_routing.py). (1) sort efficiency: per-user connection counts are tiny; acceptable. (2) _as_aware UTC for naive dt: DB columns are DateTime(timezone=True) so values are tz-aware; safe. (3) dead-pinning: review calls it 'robust' — no action. (4) IMPORTANT carried to Slice 3: the in-memory TurnPinClaimStore asyncio.Lock is the DB-free testable logic layer (and single-instance prod, numReplicas=1, is fine today), but Slice 3 must implement the REAL claim as the DB-level conditional UPDATE (WHERE served_by_connection_id IS NULL OR =:me OR <dead>) returning rowcount==1 — exactly what the plan §arch-decision-4 specifies. No slice-2 code change.

## Goal

Turn a connection from "one AI-provider login" into "one machine running the
connector." Providers are toggled per-connection; agents detach from
connections and carry a stored `provider`; each turn routes to any live
connection that covers the agent's provider, sticky-per-match with failover.
Hermes/OpenClaw connector adapters stay out of scope; their connections keep
working unchanged.

## Architecture decisions

1. **Provider is stored on the agent, never re-derived.** New `agents.provider`
   (enum) is the single value routing and gameplay read. The model→provider
   direction (`PROVIDER_MODELS`, `app/config.py:145`) is used in exactly three
   places — the create-time dropdown group→provider assignment, the migration
   reverse-map backfill, and the new uniqueness assertion — never as a live
   routing lookup. Reason: Hermes/OpenClaw have empty model allowlists
   (`PROVIDER_MODELS["hermes"] == []`), so their provider cannot be derived from
   a model. (reuse-report rows 3, "duplication risk 2/3".)
   - **Bots have no provider.** `AgentKind.BOT` agents are connectionless and
     often version-less (`app/engine/sims/seating.py:125`); they never poll a
     connection and never route by provider. So `agents.provider` is
     **nullable at the column level** with a CHECK constraint **"a non-archived
     AI agent must have a provider; bots have none"** —
     `kind=AI AND archived_at IS NULL ⇒ provider IS NOT NULL`;
     `kind=BOT ⇒ provider IS NULL`. (Archived AI agents may be NULL — see the
     orphan-archive rule below.) Mirrors the existing "a bot never has a
     connection" check. Routing only ever reads `provider` for active AI
     agents, where it is guaranteed present.
2. **`connections.provider` is retained nullable, not dropped this run.** It is
   the legacy connection identity that setup-script selection, labels, and
   hermes/openclaw still read. Machine connections leave it NULL; the eventual
   drop is the follow-up adapter run. Per-provider toggles live in the new
   `connection_providers` table, which is what routing joins against.
3. **Eligibility + the atomic pin claim live in a new DB-free
   `app/engine/turn_routing.py`; `select_next_turn` is reused unchanged for
   final ordering.** This is the reuse-report's biggest duplication risk
   (duplication risk 1): do NOT inline sticky/eligibility logic in
   `agent_next_turn.next_turn` and re-implement ordering. `turn_routing.py`
   answers "is this (agent,match) eligible for this polling connection, and can
   it claim the pin?"; `next_turn.select_next_turn` answers "which urgent turn."
4. **The pin claim is one atomic conditional UPDATE.** Serving a turn now writes
   `players.served_by_connection_id`. To stop two concurrent polls double-serving
   one turn, the claim is `UPDATE players SET served_by_connection_id=:me,
   served_pinned_at=now WHERE id=:pid AND (served_by_connection_id IS NULL OR
   served_by_connection_id=:me OR <pinned connection is dead>)`; the poll only
   serves if `rowcount == 1`. A losing poll gets "no turn right now" and
   re-polls. No background job, no table lock. The claim runs only AFTER
   `select_next_turn` has chosen an urgent pending turn in an active match, and
   the serve path re-checks the turn is still pending before returning it, so a
   match that finalizes between selection and claim yields no turn rather than a
   zombie claim (Gemini finding 3). The "dead connection" predicate uses the
   same `LIVE_WINDOW_SECONDS` cutoff as `connection_health` so the WHERE clause
   and the health badge never disagree.
5. **`connection_health.py` is rewritten in place, not replaced.** Reuse its
   `ConnectionHealth` enum, badge map, `ConnectionHealthStatus` dataclass, and
   `LIVE_WINDOW_SECONDS` (the sticky "dead connection" check reuses that exact
   threshold). Only the `Agent.connection_id`-keyed queries change: health now
   keys off the connection's own liveness (`last_seen_at`, `runner_pid`) + the
   matches pinned to it (`players.served_by_connection_id`).
6. **Strand-detection is one shared server-side helper.** The reuse audit found
   there is NO server-side delete-confirm pattern to copy (today's "confirm" is a
   browser `confirm()`). Build one coverage query — "does any *other* live
   connection cover provider P for this user?" — used by both the
   provider-disable toggle endpoint and `delete_connection`. `delete_connection`'s
   current blanket `Agent.status = PAUSED` (`connections_lifecycle.py:105`)
   becomes coverage-aware: it stops the machine's runner but leaves agents ACTIVE;
   only now-uncovered agents surface the warning.
7. **Capacity (join gate) scales with machines.** The per-connection
   `active_match_count >= connection.max_concurrent_games` gate becomes: the
   user's active matches for agents of provider P >= SUM of
   `max_concurrent_games` over the user's live connections with P enabled.
   Note (Codex finding 3): the strand-check is intentionally **coverage-based,
   not capacity-based** — disabling/deleting a connection is allowed as long as
   *some* other live connection covers provider P at all. If the remaining
   machines are saturated, the affected matches simply wait for a slot (the
   capacity gate already queues work); they are not permanently stranded, and
   active matches keep their existing pin. Capacity-aware warnings are a
   possible follow-up, not required here.

## Reuse decisions (every reuse-report row addressed)

| Capability | Decision | Where in the plan |
|---|---|---|
| `connection_providers` table | justified-new (mirror `connection_setups` column conventions: `FlexibleEnumType`, FK+index, timestamps) | Slice 1 |
| report-pid detection | extend `_ReportPidRequest` + `report_pid` (optional `detected_providers`); connector adds a `shutil.which` sweep | Slice 3 (server), Slice 7 (connector) |
| `agents.provider` + model→provider | extend `PROVIDER_MODELS` (single source); add uniqueness assertion in `config.py`; `_provider_from_model` stays connector-fallback only | Slice 1 (column+assertion), Slices 3/6 (reads) |
| grouped availability-aware dropdown | extend `new_agent_form`'s existing `provider_models_map` to read `connection_providers` instead of `connection.provider` | Slice 6 |
| sticky pin + eligibility | extend `select_next_turn` (unchanged) + justified-new `turn_routing.py` + new player columns | Slices 1, 2, 3 |
| connection health | extend `connection_health.py` (swap only the `Agent.connection_id` queries) | Slice 4 |
| destructive confirm | justified-new server-side strand-detection helper; reuse JS `confirm()` idiom for the dialog | Slice 5 |
| Alembic batch mode | reuse the `op.batch_alter_table` idiom (0023 is the template); extend `tests/test_migrations.py` | Slice 1 |
| setup-script download/allowlist | reuse untouched (`_AGENT_RUNNERS`, `_SETUP_SCRIPTS`); only stop asking the user to pick a provider | Slices 5, 7 |

## Wave / slice breakdown

Each slice ends at a `[CHECKPOINT]` and targets < 300 changed lines. Slice 1 is
foundational; everything else depends on its schema. Sequenced at stable
interface boundaries (model → engine → wiring → routes → connector → templates).

**Slice 1 — Schema + migration foundation** (~250 lines)
- `app/models/connection_provider.py` (new model), `agents.provider` column on
  `app/models/agent.py`, `served_by_connection_id` + `served_pinned_at` on
  `app/models/player.py`, `connections.provider` made nullable.
- `app/config.py`: assert model names unique across non-empty-allowlist
  providers (skip hermes/openclaw).
- `migrations/versions/0026_unified_connections.py` (down_revision `0025`):
  create `connection_providers` (backfill one enabled row per connection from
  legacy `connections.provider`); add `agents.provider` (nullable) with the §6
  precedence backfill **by kind**:
  - `kind=BOT` → `provider = NULL` (bots never route by provider);
  - `kind=AI` attached → that connection's `connections.provider`;
  - `kind=AI` detached **with** a current version → reverse-map the version
    model via `PROVIDER_MODELS`;
  - `kind=AI` detached with no/unmappable model (an already-broken orphan: no
    connection AND no provider-mappable model, so it cannot play today either)
    → **archive it** (set `archived_at`, leave `provider` NULL) and emit a loud
    warning listing the ids. Do NOT abort the whole migration. Rationale
    (Codex/Gemini HIGH, both reviewers): migrations run automatically in the
    deploy path — Railway's `preDeployCommand: alembic upgrade head` and the
    `app/main.py:113` startup upgrade on other envs — so a hard `raise` would
    block the deploy/startup over a handful of already-dead rows. Archiving is
    recoverable (an admin un-archives + sets a provider) and the
    `archived_at IS NULL` CHECK still holds. `--dry-run` still lists exactly
    which agents will be archived so the operator reviews them first
    (data-critical-waves), but the live path degrades safely instead of
    crashing.
  Then add the `kind=AI ⇒ provider NOT NULL` / `kind=BOT ⇒ provider NULL` CHECK
  constraint; add player pin columns (backfill from the agent's current
  `connection_id` for active matches); drop `agents.connection_id`; make
  `connections.provider` nullable (keep). All constraint ops wrapped in
  `op.batch_alter_table`.
- **`downgrade()`** is structural, not data-restoring: re-add
  `agents.connection_id` (nullable), drop `agents.provider` + the CHECK, drop
  the pin columns, drop `connection_providers`, restore `connections.provider`
  NOT NULL only if safe. It does NOT reconstruct the old per-provider
  attachment (that information is intentionally gone forward); the migration is
  forward-only for data, reversible for schema. State this in the migration
  docstring.
- Extend `tests/test_migrations.py`: structural up/down round-trip **plus** a
  production-shaped backfill fixture that includes (a) bot agents, (b) attached
  AI agents, (c) detached AI agents with a current version, (d) a detached AI
  agent with no resolvable provider (asserts it ends **archived** with
  `provider IS NULL`, and the migration does NOT raise), and (e) a deleted
  connection. Assert bots end NULL, active AI agents end with their old
  provider, the orphan is archived, active players are pinned, the CHECK
  constraint holds, and row counts are unchanged.
- `[CHECKPOINT]`

**Slice 2 — `turn_routing.py` engine + unit tests** (~200 lines)
- `app/engine/turn_routing.py`: DB-free eligibility predicate (user match,
  provider enabled, sticky rule incl. dead-pin via `LIVE_WINDOW_SECONDS`) and
  the atomic-claim SQL builder/result interpretation.
- Tests in `app/engine/`: eligible/ineligible, sticky stays, failover when
  pinned connection dead, **two simultaneous claims → exactly one wins**, and
  **zero coverage → no candidate (not an exception)**.
- `[CHECKPOINT]`

**Slice 3 — Wire routing into the agent API** (~220 lines)
- `app/routes/agent_next_turn.py`: candidate query off `Agent.connection_id`
  onto user + `connection_providers.enabled` + pin join; call the Slice-2
  atomic claim before serving; add `"provider"` to the turn payload; extend
  `report_pid` + `_ReportPidRequest` with optional `detected_providers` →
  updates **only** `connection_providers.detected`/`detected_detail`, **never**
  `enabled` (the user's toggle is sacred). An absent `detected_providers`
  (legacy connector) is a no-op — no rows written — so old connectors can't
  mutate toggle state (Gemini residual 2).
- `app/deps.py::require_agent_player`: resolve playable seats by user +
  provider-enabled-on-this-connection + pin, not `Agent.connection_id`.
- `[CHECKPOINT]`

**Slice 4 — Health + nav + lobby + player resolution** (~250 lines)
- `app/engine/connection_health.py`: rewrite `compute_connection_health` body
  around liveness + pins (keep enum/badge/threshold). An idle-but-live machine
  (running, providers on, no matches pinned yet) is correctly READY/LIVE —
  health reflects the connector's liveness, not whether it is currently serving
  a turn (answers Gemini finding 2; not a bug).
- `app/routes/nav_context.py::user_has_connected_agent`: "user owns an AI agent
  whose provider is covered by a connection that has connected."
- `app/routes/web_lobby.py` (`:435`): the "warm agents" join that drives the
  homepage onboarding banner/CTA moves off `Agent.connection_id` to the
  provider-coverage model (Codex LOW finding — keep it consistent with the nav
  CTA).
- `app/routes/web_player.py` (`:148/181/243/296`): readiness, per-provider
  capacity (join-gate SUM), and join display off `Agent.connection_id`.
- `[CHECKPOINT]`

**Slice 5 — Connections routes (machine model)** (~280 lines)
- `connections_setup.py`: nickname-only create (drop `_PROVIDER_GROUPS`);
  provider-NULL branches in `_provider_label`, `_setup_message`,
  `connection_setup_detail`; Providers box + Recent activity box on detail;
  `POST /me/connections/{id}/providers/{provider}` toggle endpoint with
  strand-detection confirm; `ConnectionSetup.provider` nullable + NULL-bucket
  resume.
- `_load_detached_agents` (`connections_setup.py:130`) today only returns
  detached agents with `status == PAUSED`. Since the new delete leaves agents
  ACTIVE, replace it with a **coverage-based "stranded agents" query** — AI
  agents (any status) whose provider is covered by no other live connection —
  so the detail page actually surfaces the agents it must warn about (Codex
  MED finding).
- `connections_lifecycle.py`: delete becomes coverage-aware (stop runner, leave
  agents ACTIVE; strand check shared with the toggle); remove `/reattach`.
- **Connection-create sets `provider` from the (now nullable) setup** (Codex
  HIGH): `create_connection` (`connections_setup.py:281`) and the agent-API
  bootstrap path (`deps.py:202`, which creates a `Connection` from a pending
  setup) set `connection.provider = setup.provider` — which is `NULL` for
  machine setups and the legacy value for hermes/openclaw. So machine
  connections persist `provider=NULL` and the single connector is the installer
  for them; `connections.provider` remains the persisted source of truth only
  for legacy/hermes/openclaw labeling and installer selection. No new
  replacement field is needed.
- `[CHECKPOINT]`

**Slice 6 — Agents routes (creation/edit + read views off attachment)** (~300 lines)
- `agents_setup.py`: `new_agent_form` builds the grouped availability-aware
  dropdown from `connection_providers`; **`create_agent_or_connection`
  (`agents_setup.py:454`) — the combined create endpoint — derives the agent's
  provider from the chosen model's dropdown group (not from a submitted
  `provider`/`connection_id`) and stores it; its inline "connect a new AI" path
  creates a machine connection (no provider).** Remove the provider/connection
  POST params; `edit_agent_version_page`/`save_version` validate models via
  `PROVIDER_MODELS` without an attached connection; join gate uses the SUM rule.
- **Read views (Codex MED):** `_build_agent_detail_context`
  (`agents_setup.py:236`) derives `health`, `provider_label`,
  `candidate_connections`, and `join_blocked` from the attached connection, and
  `list_agents` (`agents_setup.py:339`) hard-codes detached agents to a
  "disconnected" stub. Rewire both to the stored `agent.provider` +
  provider-coverage model so a detached-but-covered agent reads as healthy.
  Both read views, the agent-detail context, and the strand-check share **one
  coverage predicate helper** (the same one from Slice 5) so list, detail, and
  delete/toggle can never disagree about whether an agent is covered (Gemini
  finding 2 / residual 2).
- **Keep `agent.provider` in sync with the model on EVERY model-changing path
  (Codex correctness gap, two rounds):** any path that changes the agent's
  effective model must re-derive and update `agent.provider` (claude/gemini/
  openai via `PROVIDER_MODELS`; hermes/openclaw keep their stored provider since
  the model is freeform). Route all of them through **one shared helper**
  (`set_agent_provider_from_model(agent, model)`) so none can drift:
  - `save_version` (`agents_setup.py`) — new frozen version,
  - `set_model` (`agents_lifecycle.py:235`) — edits the current unfrozen version
    in place,
  - `restore_version` (`agents_lifecycle.py:361`) — swaps `current_version_id`
    to an older version (re-derive from that version's model).
  Without this, editing or restoring to a cross-provider model leaves the stored
  provider stale and routing uses the wrong provider. Test: edit a Claude
  agent's model to a Gemini model → `provider` becomes `gemini`; restore a
  version whose model is OpenAI → `provider` becomes `openai`.
- `agents_lifecycle.py`: replace `PROVIDER_MODELS.get(connection.provider…)`
  reads with stored `agent.provider`. **Pause/resume must be revisited (Codex
  MED):** `resume_agent` (`agents_lifecycle.py:203`) today hard-rejects
  `agent.connection_id is None` — after the column is dropped that would reject
  every agent. Resume now simply sets `status=ACTIVE`; whether the agent can
  actually play depends on provider coverage, surfaced as the "no live
  connection runs <provider>" wait/warning state, not a hard 409. `pause_agent`
  is unchanged.
- `[CHECKPOINT]`

**Slice 7 — Connector script** (~120 lines) — *parallel-safe with 5/6*
- `scripts/agentludum_connector.py`: `shutil.which` sweep for claude/codex/
  gemini → `detected_providers` in the existing startup `report-pid` POST
  (optional, old servers/connectors unaffected); per-turn provider prefers the
  payload `provider` field, `_provider_from_model` fallback retained. Do NOT
  delete the hermes/openclaw setup scripts.
- `[CHECKPOINT]`

**Slice 8 — Templates, copy & final attachment audit** (~220 lines)
- `connections/list.html`, `connections/detail.html` (+`connection.html`),
  `agents/*`, the nav Play-CTA copy, lobby/onboarding copy, `COPY.md`. Audit
  `app/templates/` for "one connection per provider" wording.
- **Repo-wide sweep (Codex residual 2):** `grep -rn "Agent.connection_id\|\.connection_id\|connection\.provider\|PROVIDER_MODELS.get(connection"` across `app/` (routes, templates, read-side helpers). Every remaining read must be either intentionally legacy (hermes/openclaw on `connections.provider`) or migrated; resolve or annotate each hit so no mixed old/new rule survives.
- `[CHECKPOINT]`

## Parallel analysis

- Slices 1→2→3→4 are a hard chain (schema → engine → wiring → dependent reads).
- **Slice 7 (connector) is disjoint** from the route slices (different file,
  `scripts/`) and depends only on the Slice 3 payload contract being settled; it
  may run in parallel with Slices 5/6. `[P: scripts/agentludum_connector.py]`
- Slice 8 (templates) depends on Slices 5/6 route shapes and runs last.
- Given the per-slug run lock and an overnight autonomous run, implementation
  executes sequentially; the parallel-safe note is recorded for awareness, not
  forced.

## Residual risks (each with a verification action)

- **Migration mis-backfill / data loss.** verification: run `0026` against a
  seeded production-shaped DB (per the data-critical-waves rule); assert every
  connection has exactly one enabled `connection_providers` row matching its old
  provider, every `agents.provider` equals its old connection's provider
  (incl. hermes/openclaw), every active match's players are pinned to the
  pre-migration server, and connection/agent/player row counts are unchanged;
  review the `--dry-run` loud-fail list before any live run.
- **Routing double-serves a turn under concurrent polls.** verification: the
  Slice-2 unit test simulating two simultaneous pin claims asserts exactly one
  wins (`rowcount == 1` for one, `0` for the other).
- **Two connections livelock fighting for the same turn.** Once a poll wins the
  claim, the pin is set to it, so every other poll sees "pinned, not me, not
  dead" → ineligible and moves on; no repeated contention (Gemini residual 1).
  verification: a unit test where, after connection A claims a match, a poll
  from connection B for that same match returns no candidate while the pin is
  alive.
- **Bots break the migration (NOT NULL provider).** verification: the Slice-1
  migration fixture includes bot agents and asserts they end with
  `provider IS NULL` and the CHECK constraint holds (`kind=AI ⇒ NOT NULL`,
  `kind=BOT ⇒ NULL`); the migration does not loud-fail on a DB containing bots.
- **Failover doesn't pick up a dropped match.** verification (pre-merge, manual):
  run two local connectors against a dev server both covering Claude, kill the
  pinned connector mid-match, confirm the other serves the match within one
  `LIVE_WINDOW_SECONDS` window and the match completes (acceptance #4).
- **Join-gate SUM surprises users / blocks valid joins.** verification: unit
  test the gate at 0, 1, and 2 live connections with the provider enabled; the
  blocked-message copy states the SUM rule.
- **Old connector breaks against the new server.** verification: acceptance #7 —
  run the pre-change connector (checked out from git history) against the new
  server and confirm turns still serve (payload `provider` ignored,
  `detected_providers` absent).
- **An agent whose provider is covered nowhere errors instead of waiting.**
  verification: Slice-2 test asserts the eligibility helper returns "no
  candidate" (not an exception) at zero coverage; the match stays resumable once
  a covering connection returns.
- **A detached agent with no resolvable provider silently gets a wrong default
  in the migration.** verification: the `0026` backfill has no default-guess
  branch — an unresolvable orphan AI agent is **archived** (`archived_at` set,
  `provider` NULL) and logged, never assigned a guessed provider; the migration
  test feeds one such row and asserts it ends archived (not a wrong provider,
  not a crash).
- **The migration crashes the auto-deploy** (Railway `preDeployCommand` /
  `app/main.py` startup upgrade). verification: the migration never `raise`s on
  data shape — unresolvable rows archive, not abort; the migration test asserts
  a DB containing one orphan completes `upgrade head` with exit 0.

## Out of scope (unchanged from spec §7)

Hermes/OpenClaw connector adapters; renaming Connections→Machines; the
direct-MCP play path; black-box leaderboard presentation; multi-key connector;
dropping `connections.provider` (deferred to the follow-up adapter run).
