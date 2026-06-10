# Reuse Audit — unified-connections

## Summary

Almost every capability this feature needs already exists in a form that should
be **extended in place**, not rebuilt. The connector already drives all three
CLIs by model prefix (`_provider_from_model`), the model→provider source of
truth already lives in `PROVIDER_MODELS`, the turn-routing core
(`select_next_turn`) is already DB-free and unchanged by the spec, the connector
already calls `report-pid` on startup, and a grouped/availability-aware model
dropdown already half-exists in the agent-create form. The genuinely new pieces
are narrow and structural: the `connection_providers` child table (no existing
per-connection settings table to mirror — `connection_setups` is a one-shot
draft row, not a recurring settings pattern), the two sticky-pin columns on
`players`, and the new DB-free `turn_routing` eligibility helper the spec
explicitly asks to extract. Two spec assumptions don't match the code and are
flagged below: there is **no server-side delete-confirm pattern** to copy (it's a
one-line JS `confirm()`), and `connection_health.py` is a real ~224-line module
(not the "~120 planned" stub the architecture doc still describes).

## Capability table

| Capability | Existing module (path:line) | Verdict | Note |
|---|---|---|---|
| Per-connection provider toggle storage (`connection_providers` table) | `app/models/connection_setup.py:15` (the only per-connection child table); `app/models/connection.py:29` | **justified-new** | No reusable per-connection *settings* child table exists. `connection_setups` is a one-shot draft keyed by `(user_id, provider)`, not a queryable per-provider toggle row. Mirror its column conventions (`FlexibleEnumType`, FK + index, `created_at`/`updated_at`) but the table itself is new — and the spec wants it as a table (not JSON) precisely so it can join in routing. |
| Provider/CLI detection reporting from the connector (extend report-pid) | endpoint `app/routes/agent_next_turn.py:194` (`report_pid` + `_ReportPidRequest:190`); connector call `scripts/agentludum_connector.py:560` | **extend** | Endpoint and the startup POST both already exist. Add an optional `detected_providers: list[str]` to `_ReportPidRequest` (must stay optional for old connectors — acceptance #7) and a `shutil.which` sweep before the existing `httpx.post`. Do not add a second endpoint. |
| Agent `provider` storage + model→provider mapping | `app/config.py:145` (`PROVIDER_MODELS`); connector `_provider_from_model` `scripts/agentludum_connector.py:427` | **extend** | `PROVIDER_MODELS` is the single source of truth and already encodes empty lists for hermes/openclaw (the reason the column must be *stored*, not derived). Reuse it for the create-time group→provider mapping and the migration's reverse-map backfill. Add the uniqueness assertion (§1) in `config.py` next to `PROVIDER_MODELS`. `_provider_from_model` stays as the connector's fallback only. The new `agents.provider` column is new but small. |
| Grouped, availability-aware model dropdown in agent creation | form route `app/routes/agents_setup.py:384` (`new_agent_form`) building `provider_models_map` at `:438`; `_PROVIDER_GROUPS` `app/routes/connections_setup.py:36` | **extend** | The create form already computes `provider_choices` from the user's *active* connections (`:404`) and ships a per-provider `provider_models_map` (`:438`) — that is 80% of "grouped + availability-aware." Rework it to read enabled providers from `connection_providers` instead of `connection.provider`, and drop the connection/provider POST params. `_PROVIDER_GROUPS` in `connections_setup.py` is the connection-create grouping the spec *removes*; do not confuse the two — the agent dropdown grouping is the one to keep and extend. |
| Sticky pin columns + turn-routing eligibility selection | `app/engine/next_turn.py:24` (`select_next_turn`); endpoint `app/routes/agent_next_turn.py:50` (`next_turn`); `app/models/player.py:11` | **extend (selection) + justified-new (columns + helper)** | `select_next_turn` is already DB-free and the spec says its priority is **unchanged** — reuse as-is. The two pin columns (`served_by_connection_id`, `served_pinned_at`) are new on `players` (no analog today). The eligibility/sticky/atomic-claim logic is new but the spec mandates extracting it into a new DB-free `app/engine/turn_routing.py` so it sits beside `next_turn.py` and is unit-testable — justified-new, not a rebuild of `select_next_turn`. The candidate query in `next_turn` (`:56`) is **extended** off `Agent.connection_id` onto the user+enabled-provider+pin join. |
| Connection health / liveness (READY/LIVE/STALLED/DISCONNECTED) | `app/engine/connection_health.py:43` (enum), `:102` (`compute_connection_health`); staleness `LIVE_WINDOW_SECONDS:18` | **extend** | Reusable: the `ConnectionHealth` enum, `_HEALTH_PRESENTATION` badge map (`:53`), `ConnectionHealthStatus` dataclass (`:62`), `_as_aware`/`_humanize_since`, and `LIVE_WINDOW_SECONDS` (the §2 "dead pin" check reuses this threshold unchanged). Must-change: every query keyed on `Agent.connection_id` (`:151`, `:172`) — these resolve health through agent attachment, which breaks after detachment. Rewrite the body around connection liveness (`last_seen_at`, `runner_pid`) + matches pinned via `players.served_by_connection_id`. Note: the architecture doc (`AGENT_LUDUM_ARCHITECTURE.md:110`) still calls this "~120 (planned, slice 4)"; it is a real 224-line shipped module — update that doc line. |
| Destructive-action confirm step (disabling a provider that strands agents) | delete form `app/templates/connections/detail.html:82` (`onsubmit="return confirm(...)"`); same pattern `app/templates/agents/detail.html:133` | **justified-new (server step) / extend (JS copy)** | There is **no server-side confirm step** to copy. Today's "confirm" is a browser `confirm()` on the delete form — fine for the toggle's client warning, but the spec needs a server-side coverage check ("would this strand agents?") that the JS dialog can't do. The coverage check is new logic shared by both the disable-toggle endpoint and `delete_connection`. Reuse the JS `confirm()` idiom for the dialog; build the strand-detection server-side. Flag: spec text says "same pattern as delete confirm in `connections_lifecycle.py`" — `delete_connection` (`connections_lifecycle.py:80`) has **no** confirm logic, so the plan must define this fresh. |
| Alembic migration with SQLite batch mode | `migrations/versions/0023_connection_agent_split.py:298,317` (`op.batch_alter_table`); template `migrations/versions/0025_add_connection_deleted_at.py`; guard `tests/test_migrations.py` | **reuse (pattern)** | The repo already wraps every constraint/column-drop in `op.batch_alter_table` (0023, 0011, 0015, 0019, 0022 all do). Follow that exact idiom for dropping `agents.connection_id` and adding NOT-NULL `agents.provider`. `tests/test_migrations.py` already asserts up/down round-trips on SQLite — the new migration is covered by extending it, no new test harness. Plain `add_column` (0025) is the template for the additive steps. |
| Setup-script download/allowlist + per-provider setup-script selection | `_AGENT_RUNNERS` `app/routes/web_player.py:96` + `_serve_agent_file:106`; `_SETUP_SCRIPTS` + `_setup_script_name` `app/routes/connections_setup.py:56,79` | **reuse** | Both the download allowlist (`_AGENT_RUNNERS`, exact-filename, no traversal) and the per-provider selector (`_SETUP_SCRIPTS`) already exist and already map claude/gemini/openai to the one `agentludum_connector.py`. The spec explicitly says **no change needed** for the CLI trio and that hermes/openclaw aliases must stay. Reuse untouched; just stop *asking* the user to pick a provider at connection-create time. |

## Duplication risks

1. **A second next-turn/eligibility code path.** The biggest risk is writing the
   new sticky/eligibility logic *inline* in `agent_next_turn.next_turn` and
   duplicating selection ordering that already lives in
   `app/engine/next_turn.select_next_turn`. The spec's own instruction — extract a
   DB-free `app/engine/turn_routing.py` and keep `select_next_turn` for final
   ordering — is the right split; the plan must enforce it so the two don't
   diverge. Reuse `select_next_turn` for "which urgent turn," add `turn_routing`
   only for "is this (agent,match) eligible for *this* connection + claim the pin."

2. **A parallel model→provider map.** The grouped dropdown, the create-time
   provider assignment, and the migration backfill all need model→provider. All
   three must read the single `PROVIDER_MODELS` (`app/config.py:145`) — do not
   hand-roll a second dict in `agents_setup.py` or the migration. The connector's
   `_provider_from_model` is a *separate* fallback and must not become a third
   source of truth on the server side.

3. **Re-deriving provider where it's now stored.** After `agents.provider` lands,
   the many `PROVIDER_MODELS.get(connection.provider.value, ...)` reads
   (`connections_setup.py:150,376,394`, `agents_setup.py:290,303,502`,
   `connections_lifecycle.py:136`, `web_player.py:296`) must switch to the
   stored `agent.provider` + `connection_providers.enabled`. Leaving any of them
   reading `connection.provider` recreates the old per-provider-login coupling
   the feature is removing.

4. **Two confirm mechanisms.** Don't add a server-side confirm to the
   provider-toggle while leaving delete on the JS-only `confirm()` — and don't
   copy the JS dialog as if it were the strand-check. The strand-detection
   coverage query should be one shared helper used by both the toggle-disable
   endpoint and `delete_connection` (`connections_lifecycle.py:80`), whose
   blanket `Agent.status = PAUSED` (`:105`) must itself change to a
   coverage-aware update.

5. **A new health module.** `connection_health.py` already exists with the exact
   enum, badge map, and staleness window the feature reuses. Rewriting it from
   scratch (instead of swapping only the `Agent.connection_id` queries for
   liveness + pin queries) would throw away the reusable presentation layer and
   the `LIVE_WINDOW_SECONDS` threshold the sticky "dead pin" rule depends on.
