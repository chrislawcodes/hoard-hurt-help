# Tasks — unified-connections

Each slice ends at a `[CHECKPOINT]` and targets < 300 changed lines. Verification
for every slice = the Preflight Gate from CLAUDE.md run at the repo root:
`python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`. Slices are
ordered by dependency: schema → engine → wiring → dependent reads → routes →
connector → templates/sweep. Source of truth: `plan.md` (+ its Review
Reconciliation directives) and `spec.md`.

## Slice 1 — Schema + migration foundation  (~250 lines) [deps: none]

- [ ] Add `app/models/connection_provider.py`: `ConnectionProvider` row
  (`connection_id` FK+index, `provider` enum via `FlexibleEnumType`, `enabled`
  bool, `detected` bool, `detected_detail` str|None, `created_at`/`updated_at`).
  Mirror `connection_setup.py` column conventions.
- [ ] `app/models/connection.py`: make `provider` `nullable=True` (retain;
  legacy/hermes-openclaw identity). Add the `enabled_providers` relationship.
- [ ] `app/models/agent.py`: add `provider` enum column (nullable); drop
  `connection_id` mapping (column drop happens in the migration). Add the
  CHECK invariant `kind=AI AND archived_at IS NULL ⇒ provider NOT NULL` and
  `kind=BOT ⇒ provider NULL`.
- [ ] `app/models/player.py`: add `served_by_connection_id` (nullable FK→connections)
  and `served_pinned_at` (nullable datetime).
- [ ] `app/config.py`: assert model names unique across non-empty-allowlist
  providers (skip hermes/openclaw); add `provider_for_model(model)` helper that
  reverse-maps via `PROVIDER_MODELS` (single source of truth).
- [ ] `app/models/connection_setup.py`: make `provider` nullable.
- [ ] `migrations/versions/0026_unified_connections.py` (down_revision `0025`):
  create `connection_providers` (+ backfill one enabled row per connection from
  legacy `connections.provider`); add `agents.provider` and backfill by kind
  (BOT→NULL; AI attached→connection.provider; AI detached+version→reverse-map;
  AI unresolvable→archive + warn, never raise); add the CHECK; add player pin
  columns (+ backfill pins from agent's current `connection_id` for active
  matches); drop `agents.connection_id`; keep `connections.provider` nullable.
  All constraint ops in `op.batch_alter_table`. `downgrade()` is schema-only
  (documented in the docstring).
- [ ] Extend `tests/test_migrations.py`: structural up/down round-trip + a
  production-shaped fixture (bot, attached AI, detached AI w/ version, detached
  AI unresolvable → asserts archived not raised, deleted connection). Assert
  bots NULL, active AI = old provider, orphan archived, pins set, CHECK holds,
  counts unchanged, `upgrade head` exits 0.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 2 — turn_routing engine + unit tests  (~200 lines) [deps: 1]

- [ ] Add `app/engine/turn_routing.py` (DB-free): eligibility predicate (same
  user, agent.provider enabled on the polling connection, sticky rule incl.
  dead-pin via `LIVE_WINDOW_SECONDS`) and the atomic-claim SQL builder +
  rowcount interpretation. One shared `agent_is_covered(...)` coverage predicate
  reused by routing, strand-check, and health.
- [ ] Tests in `app/engine/`: eligible/ineligible; sticky stays; failover when
  pinned connection dead; two simultaneous claims → exactly one wins; zero
  coverage → no candidate (no exception); after A claims, B polling same match
  → no candidate while pin alive.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 3 — Wire routing into the agent API  (~220 lines) [deps: 2]

- [ ] `app/routes/agent_next_turn.py`: candidate query off `Agent.connection_id`
  → user + `connection_providers.enabled` + pin join; call the Slice-2 atomic
  claim before serving; only serve on `rowcount==1`; add `"provider"` to the
  turn payload.
- [ ] `report_pid` + `_ReportPidRequest`: optional `detected_providers` → update
  only `connection_providers.detected`/`detected_detail`, never `enabled`;
  absent field = no-op.
- [ ] `app/deps.py::require_agent_player`: resolve playable seats by user +
  provider-enabled-on-this-connection + pin, not `Agent.connection_id`.
- [ ] Tests: routing serves the right agent; report-pid with/without
  detected_providers; old payload (no provider) still works.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 4 — Health + nav + lobby + player resolution  (~250 lines) [deps: 3]

- [ ] `app/engine/connection_health.py`: rewrite `compute_connection_health`
  around liveness (`last_seen_at`, `runner_pid`) + pinned matches; keep enum,
  badge map, `LIVE_WINDOW_SECONDS`. Idle-but-live = READY/LIVE.
- [ ] `app/routes/nav_context.py::user_has_connected_agent`: provider-coverage
  model.
- [ ] `app/routes/web_lobby.py:435`: warm-agents join off `Agent.connection_id`.
- [ ] `app/routes/web_player.py` (`:148/181/243/296` + `player_dashboard`
  `:427/443`): readiness, per-provider capacity (join-gate SUM), join display,
  and dashboard rendering off `Agent.connection_id`.
- [ ] Tests: health for live/idle/stale/dead; join-gate sum at 0/1/2 live
  connections; player_dashboard renders a detached-but-covered agent.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 5 — Connections routes (machine model)  (~280 lines) [deps: 3] [P: app/routes/connections_setup.py, app/routes/connections_lifecycle.py]

- [ ] `connections_setup.py`: nickname-only create (drop `_PROVIDER_GROUPS`);
  `create_connection` + `deps.py:202` bootstrap set `provider=setup.provider`
  (NULL for machine); provider-NULL branches in `_provider_label`,
  `_setup_message` (single connector), `connection_setup_detail`; Providers box
  + Recent activity box on detail.
- [ ] `_load_detached_agents` → coverage-based stranded-agents query (AI agents,
  any status, covered by no other live connection).
- [ ] `POST /me/connections/{id}/providers/{provider}` toggle endpoint with
  shared strand-detection confirm helper.
- [ ] `ConnectionSetup.provider` nullable + NULL-bucket resume
  (`_load_resumeable_pending_setup` for machine drafts).
- [ ] `connections_lifecycle.py`: delete = coverage-aware (stop runner, leave
  agents ACTIVE; shared strand check); remove `/reattach`.
- [ ] Tests: machine create has no provider; toggle-disable strand confirm;
  delete leaves covered agents ACTIVE; hermes/openclaw detail page still loads.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 6 — Agents routes (creation/edit + read views)  (~300 lines) [deps: 3]

- [ ] `agents_setup.py`: `new_agent_form` grouped availability-aware dropdown
  from `connection_providers`; `create_agent_or_connection` (`:454`) derives
  provider from the chosen model's group, inline-connect makes a machine
  connection; remove provider/connection POST params; `edit_agent_version_page`
  validates models without an attached connection; join gate = SUM rule.
- [ ] Rewire `_build_agent_detail_context` (`:236`) and `list_agents` (`:339`)
  to stored `agent.provider` + the shared coverage predicate (detached-but-
  covered = healthy).
- [ ] One shared `set_agent_provider_from_model(agent, model)` helper; call it
  from `save_version`, `set_model` (`agents_lifecycle.py:235`), and
  `restore_version` (`agents_lifecycle.py:361`).
- [ ] `agents_lifecycle.py`: `resume_agent` drops the `connection_id is None`
  rejection (just set ACTIVE); replace `PROVIDER_MODELS.get(connection.provider…)`
  reads with stored `agent.provider`.
- [ ] Tests: create stores provider from model group; edit Claude→Gemini model
  flips provider; restore version re-derives provider; resume a detached agent
  succeeds.
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 7 — Connector script  (~120 lines) [deps: 3] [P: scripts/agentludum_connector.py]

- [ ] `scripts/agentludum_connector.py`: `shutil.which` sweep for
  claude/codex/gemini → `detected_providers` in the startup `report-pid` POST
  (optional; old servers unaffected). Per-turn provider prefers the payload
  `provider` field; `_provider_from_model` fallback retained; model field used
  as today. Do NOT delete the hermes/openclaw setup scripts.
- [ ] Manual/scripted check: connector still starts and serves a turn against a
  dev server (no live-LLM dependency in the unit layer).
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Slice 8 — Templates, copy & final attachment sweep  (~220 lines) [deps: 4,5,6]

- [ ] `connections/list.html`, `connections/detail.html` (+`connection.html`),
  `agents/*`: machine cards, provider ✓/✗ summary, stranded warning,
  `model · vN`, "No live connection runs <provider>" copy; footer explainer.
- [ ] Nav Play-CTA + lobby/onboarding copy off the per-provider model; `COPY.md`.
- [ ] Repo-wide sweep:
  `grep -rn "Agent.connection_id\|\.connection_id\|connection\.provider\|PROVIDER_MODELS.get(connection" app/`
  — every remaining read is intentionally legacy (hermes/openclaw) or migrated;
  resolve or annotate each hit (incl. player_dashboard, onboarding/status
  fragments).
- [ ] Preflight Gate green.
- [ ] `[CHECKPOINT]`

## Verification (pre-merge, beyond per-slice preflight)

- [ ] Migration on a seeded production-shaped DB copy: per-acceptance #6 + #8
  assertions, `--dry-run` orphan list reviewed.
- [ ] Manual failover (acceptance #4): two local connectors covering Claude,
  kill the pinned one mid-match, confirm the other takes over within one
  `LIVE_WINDOW_SECONDS` and the match completes.
- [ ] Old connector (acceptance #7): pre-change connector from git history still
  serves turns against the new server.
