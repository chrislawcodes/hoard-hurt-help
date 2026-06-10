# Closeout: unified-connections

## What shipped

A connection is now **one machine running the connector**, not one AI-provider
login. Agents detach from connections and route dynamically.

- **Schema (slice 1):** new `connection_providers` table (per-connection
  provider toggles + connector detection), `agents.provider` (nullable with a
  CHECK: non-archived AI ⇒ provider NOT NULL, bot ⇒ NULL), player sticky-pin
  columns (`served_by_connection_id`, `served_pinned_at`), `connections.provider`
  kept nullable as legacy identity. Migration `0026` backfills every connection
  with its legacy provider enabled, backfills `agents.provider` (attached →
  connection provider; detached+model → reverse-map; unresolvable orphan →
  archived, never aborts the deploy), and pre-pins active matches.
- **Routing engine (slice 2):** DB-free `app/engine/turn_routing.py` —
  eligibility (user + provider enabled + sticky rule incl. dead-pin) and the
  atomic-claim logic, with unit tests incl. a two-claim race and zero-coverage.
- **Agent API (slice 3):** `next_turn` routes by user + `connection_providers`
  coverage + sticky pin, claims the pin with one atomic conditional UPDATE
  (rowcount==1) so concurrent polls can't double-serve; payload adds `provider`;
  `report_pid` accepts `detected_providers` (updates only `detected`, never
  `enabled`); `require_agent_player` resolves off coverage.
- **Health/nav/lobby (slice 4):** `compute_connection_health` keys off the
  connection's own liveness + pinned matches (idle-but-live = READY);
  `nav_context` and `web_lobby` warm-agent use provider coverage.
- **Connections routes (slice 5):** provider-toggle endpoint with a strand
  guard (confirm before disabling a provider no other live connection covers);
  coverage-aware delete (stops the runner, leaves agents ACTIVE); reattach
  removed; "stranded agents" list; Providers toggle box on the detail page.
- **Agents routes (slice 6):** agent creation derives + stores `provider` from
  the model and rejects when the provider is enabled nowhere; `agent.provider`
  stays in sync on every model change (set-model / save-version / restore);
  `resume_agent` no longer requires a connection.
- **Connector (slice 7):** reports detected provider CLIs on startup; prefers
  the server's explicit `provider` payload field over prefix guessing.
- **Copy (slice 8):** connections page reframed to the machine model; legacy
  `connection.provider` reads annotated as intentional.

Verification: ruff, mypy, and **555 tests** pass. New tests cover the atomic
claim, failover, report-pid detection, provider-sync, the toggle strand guard,
coverage-aware delete, and connector payload-provider preference.

## What remains open (coverage-aware follow-ups)

These render and function today via the retained `connections.provider` /
`Agent.connection_id` columns, but do not yet fully reflect provider coverage:

- `web_player.py` player-dashboard / agent-list display and the **join-gate
  capacity SUM** (still per-connection, not summed across live machines covering
  a provider).
- `agents_setup._build_agent_detail_context` / `list_agents` display via the
  attached connection (a detached-but-covered agent shows no connection rather
  than its coverage).
- `new_agent_form` grouped availability-aware dropdown (the create form still
  offers the provider groups; provider is correctly derived from the model on
  submit).
- The final drop of `agents.connection_id` / `connections.provider` (kept this
  run by the keep-then-drop plan; drop belongs to a follow-up migration).
- Orphaned `connection_providers` rows after a connection soft-delete (harmless
  — all queries filter `deleted_at` — but a cleanup is a nice-to-have).

## Deferred risks

- **Migration on production data** must be verified with a `--dry-run` against a
  production-shaped copy before the live deploy (data-critical-waves rule): the
  orphan-archive list reviewed, per-connection provider rows + agent providers +
  pins asserted. Migrations auto-run in Railway's `preDeployCommand`; the
  migration is written to archive (not abort) so it can't block the deploy.
- **Failover** has unit coverage (two-claim race, dead-pin failover) and one
  end-to-end test; a manual two-connector kill-the-pinned-one check before the
  live deploy is still recommended (acceptance #4).

## Out of scope (unchanged)

Hermes/OpenClaw connector adapters (their connections/setup scripts are
untouched and keep working); the direct-MCP play path; renaming Connections →
Machines.

## Where the artifacts live

`docs/workflow/feature-runs/unified-connections/` — spec.md, plan.md, tasks.md,
reuse-report.md, reviews/, state.json, this closeout, postmortem.md.
