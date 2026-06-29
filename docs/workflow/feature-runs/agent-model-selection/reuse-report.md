# Reuse audit — Agent model selection (slices 2–4)

No-duplication audit for the Feature Factory plan. For each capability slices 2–4
need, this lists the closest existing code and an adversarial verdict
(reuse / extend / justified-new — preferring reuse or extend). All paths are in
this worktree (`/Users/chrislaw/hoard-hurt-help--feat-agent-model-selection`) and
were grepped/read directly.

Slice 1 (the backend foundation) already shipped (#572): `Agent.preferred_model`
(migration `0044`) + the three-layer `resolve_seat_model`
(`app/engine/model_provider_match.py:35`). This audit covers only what slices 2–4
add on top.

## Capability table

| capability | existing module (path) | verdict (reuse/extend/justified-new) | note |
|---|---|---|---|
| 1. Per-(connection, provider, model) verification-result store (status + error + timestamp) | `app/models/connection_provider.py` (table `connection_providers`, UNIQUE `(connection_id, provider)`, cols `detected` / `detected_detail` / `updated_at`) — the closest shape; `app/models/agent.py:55` `preferred_model`; no `model_verifications` table exists (grep of `app/models/` + `migrations/versions/` = none) | **justified-new** | A new table is genuinely required and the spec already mandates it (Key entities). `connection_providers` is unique per `(connection_id, provider)` so it physically cannot hold multiple models per provider — adding a `model` dimension would break its unique key and overload a per-provider toggle row with per-model state. Build `model_verifications` keyed `(connection, provider, model)` with `status` / `error_text` / `last_checked_at`. **Reuse the patterns**, not the table: mirror `connection_provider.py`'s column style (`updated_at` server-default + onupdate) and the `detected`/`detected_detail` "outcome + detail" pairing. New ORM model + new Alembic migration in `migrations/versions/` (next id after `0044`). |
| 2. Server down-channel: a connection's verification worklist | `app/routes/agent_next_turn.py` (`next_turn` GET `:21`, `next_turns` GET `:32`, `report_pid` POST `:84`, all `Depends(require_connection)`); `app/deps.py` `require_connection:142` resolves `X-Connection-Key` → `Connection` | **justified-new (endpoint), reuse (auth + module + payload source)** | The spec is explicit (Reporting channels): the worklist MUST be a dedicated endpoint, NOT a field on the turn poll, because the idle connector takes `sleep; continue` and discards the poll body (`agentludum_connector.py:1372-1373`). So a new GET endpoint is justified. But it is a thin sibling of `report_pid` in the **same module** (`agent_next_turn.py`), reusing `require_connection` verbatim and sourcing `(provider, model)` pairs from the already-shipped `resolve_seat_model` / `PROVIDER_MODELS` (`app/config.py:156`) — do not invent a parallel resolver. |
| 3. Server up-channel: receive verification results + play-time failure reasons | `app/routes/agent_next_turn.py:84` `report_pid` (POST, **204 fire-and-forget**, `_ReportPidRequest` body at `:40`, writes via `_apply_detected_providers:46`); existing `is_connector_fallback` flag plumbed end-to-end (`app/schemas/agent.py:325,334`; `app/routes/agent_api.py:76,101`; `app/engine/agent_play.py:220,322`; → `TurnSubmission.was_defaulted`) | **extend the report_pid pattern (new endpoint); reuse the fallback-flag plumbing** | `report_pid` is the exact template to copy: connection-authed, 204, fire-and-forget, writes a per-connection child table. Add a sibling POST that writes `model_verifications`. The play-time **reason** MUST ride this up-channel, NOT the submit body (spec FR-009): a missed-deadline turn returns `None` and never POSTs (`agentludum_connector.py:836`), so a reason on submit would never leave the machine. `is_connector_fallback` already says *that* a fallback happened — extend the up-channel to carry *why*; do not widen `SubmitRequest`. |
| 4. Connector periodic side-task loop / self-report (where a ~60s verification tick + test call hooks in) | `scripts/agentludum_connector.py`: `_report_pid:716` on `_DETECT_REPORT_INTERVAL = 300` (`:117`), fired in the main loop at `:1293-1294`; idle branch `time.sleep(data.get("next_poll_after_seconds", 5)); continue` at `:1372-1373`; CLI runner `_run:350` (timeout from `_TURN_TIMEOUT = 180` at `:75`, per-turn budget via `_call_timeout` ContextVar); model resolution `_resolve:742` | **extend (reuse the loop + runner; add a second timed tick)** | The recurring-timed-task pattern already exists — copy the `_DETECT_REPORT_INTERVAL` monotonic-clock gate at `:1293` to add a second `_VERIFY_INTERVAL` (~60s idle) tick right beside it. **Reuse `_run` for the test call** (`_run(["claude","--print","--model",m], stdin_input="ok")`) but pass a dedicated short timeout (~30s, spec FR-005) instead of `_TURN_TIMEOUT` — `_run` reads `_call_timeout`, so set that ContextVar low for the verify path; do NOT spawn a separate subprocess helper. The test call must run off the live-turn worker pool (`_MAX_CONCURRENCY`) so it can't burn a turn deadline. Provider→default-model already lives in the adapters (`default_model`); the worklist comes from the down-channel. |
| 5. Per-model verification STATUS display (checking/verified/failed/timeout/waiting) | `app/engine/connection_health_badge.py` `ConnectionHealth` enum (`:65`) + `_HEALTH_PRESENTATION` map (`:75` → `(label, badge_class, pulse)`) + `ConnectionHealthStatus` dataclass; `app/engine/provider_readiness.py` `ProviderReadiness` (`:254`); presenter `app/routes/agents_health_presenter.py`; templates `app/templates/agents/_status.html`, `connections/_health_badge.html` (badge CSS `badge-ok`/`badge-alert`/`badge-soon`/`badge-done` + `dot`/`dot-still`) | **extend / mirror (new enum, reused badge machinery)** | The "enum → presentation tuple → badge template" pattern is exactly right and should be mirrored, not rebuilt. Add a small `ModelVerificationStatus` enum (`waiting`/`checking`/`verified`/`failed`/`timeout`) with its own `(label, badge_class, pulse)` map, and **reuse the existing badge CSS classes + dot animation** and the HTMX-polled fragment pattern (`_status.html` polls every ~15s). Do not fold model states into `ConnectionHealth` (different axis — a login can be LIVE yet a model FAILED). A new `_model_status.html` fragment + a presenter helper alongside `agents_health_presenter.py`. |
| 6. Agent-settings page + form handling (where a "preferred model" picker lives) | `app/routes/agents_detail.py` `agent_detail` GET `:219` (template `agents/detail.html`, ctx via `_build_agent_detail_context:125`); form POSTs in `app/routes/agents_lifecycle.py` (`rename_agent:133`, `save_version:272` via `_apply_version_edit:112`); template `app/templates/agents/detail.html` (sections: head/status → rename form → version card → versions history → matches → controls); model picker source `PROVIDER_MODELS` (`app/config.py:156`) | **reuse (add one form action + one template control)** | No new page or framework needed. Add a POST `set-preferred-model` action mirroring `rename_agent` (Form field → validate against `PROVIDER_MODELS` → save `Agent.preferred_model` → redirect), and a "Preferred model (advanced)" `<select>` in `detail.html` (natural slot: just after the version card). `Agent.preferred_model` already exists and is **not currently surfaced anywhere** in routes/templates (grep) — this is pure surface work over a shipped column. The select must list only `PROVIDER_MODELS` entries + a "Provider default" option (spec FR-001). |
| 7. Join-flow warning surface | `app/routes/web_join.py` `join_form:144` / `join_submit:332`, `_build_ai_options:54` (states ready/idle/not_connected/busy from `provider_readiness`); ctx dict at `:217-238` carries `error`; template `app/templates/join.html:13` `{% if error %}<p class="error">…` | **reuse (the existing warning slot + ctx); add a new read path) | The join page already has a single banner slot (`error` in ctx → `join.html:13`) and already computes per-provider readiness for the AI picker — add a sibling **warn** message (FR-014: warn, do not block) into the same ctx, rendered with the same `error`/notice pattern. The one genuinely new piece is the **read path**: the union of the user's live machine connections' `(connection, provider, model)` statuses (warn only when verified-failing on every covering connection and none verified). That query reads the new `model_verifications` table — no per-model read path exists in join today (confirmed). No model picker is added (FR-011). |
| 8. Error-text sanitization / truncation helpers (paths/tokens) | **None.** Repo-wide grep for `sanitize`/`redact`/`truncate`/`mask`/path-strip = nothing; only ad-hoc `text[:300]` slices **inside the connector** for RuntimeError messages (`agentludum_connector.py:166,388,436,443,475,478,513,551`); incident capture stores `str(exc)` raw (`app/request_logging.py:86`) | **justified-new (small shared helper)** | No reusable sanitizer exists. The connector's scattered `[:300]` slices truncate but do **not** strip absolute paths or token-shaped substrings, and they live in the wrong place (the connector, not the server that renders to the UI). Build one small pure helper (cap 300 chars + strip `/…`/`C:\…` paths + mask `sk_…`/bearer tokens, spec FR-015) and call it where the up-channel reason is stored/displayed. Keep it tiny and well-named (e.g. `app/engine/verification_error_text.py`) — no `utils.py`. |
| 9. Provider readiness pattern to mirror for model readiness | `app/engine/provider_readiness.py`: `ProviderReadiness` enum (`:254` — `NO_MCP_CONNECTION`/`CONNECTED_NOT_LIVE`/`SEEN_NOT_POLLING`/`LIVE`) + `provider_readiness():267` cascading over `provider_loop_running` / `provider_has_live_current_setup` / `provider_has_current_setup` (shared query `_provider_connections_query:38`); `connection_health_badge.py` liveness windows `LIVE_WINDOW_SECONDS=90` / `LOOP_RUNNING_WINDOW_SECONDS=120` | **reuse (as the template to mirror) + reuse its liveness predicates** | This is the canonical "one answer to is-this-ready, cascaded worst→best, with a badge map" shape the feature should copy for model readiness — but model readiness is a **different axis** (per `(connection, provider, model)` verification cache), so it is a new enum + new resolver, not a change to `ProviderReadiness`. **Reuse the liveness primitives**: the FR-014 join guard ("live machine connections covering the provider") should reuse `provider_loop_running` / the `LIVE_WINDOW_SECONDS` staleness check rather than re-deriving "is this connection live", and exclude paused/MCP exactly as `_provider_connections_query` already does. |

## Duplication risks (where the plan could rebuild something that exists)

1. **A second "is this connection live" definition.** The join guard (FR-014) and
   the down-channel worklist both need "live machine connections covering provider
   X." `provider_readiness.py` (`provider_loop_running`, `provider_has_live_current_setup`,
   `_provider_connections_query`) + the `LIVE_WINDOW_SECONDS`/`LOOP_RUNNING_WINDOW_SECONDS`
   constants already answer this and already exclude paused/MCP connections. Reuse
   them; do not hand-roll a fresh "last_seen within N seconds" query in the join or
   worklist code.

2. **A parallel model resolver.** `resolve_seat_model` /
   `model_for_provider` / `default_model_for_provider` / `provider_for_model`
   (`app/engine/model_provider_match.py`) + `PROVIDER_MODELS` (`app/config.py:156`)
   are the single source for "which model for this (provider, preferred)" and
   "which provider owns this model." The down-channel worklist and the
   effective-model display (FR-010) must read these, not re-list models or
   re-derive provider→default. The spec is explicit that resolution does **not**
   change in slices 2–4.

3. **Re-widening the turn poll / submit body instead of new channels.** It is
   tempting to bolt the worklist onto `next_turn`'s response and the failure reason
   onto `SubmitRequest`. Both fail in exactly the states this feature targets: the
   idle connector discards the poll body (`agentludum_connector.py:1372`) and a
   missed-deadline turn never POSTs a submit (`:836`). The spec mandates dedicated
   down/up endpoints — `report_pid` (`agent_next_turn.py:84`) is the reuse template
   for the up-channel shape, not the submit body.

4. **A new status-badge system.** `ConnectionHealth` + `_HEALTH_PRESENTATION` +
   the `badge-*`/`dot` CSS + the HTMX-polled `_status.html` fragment are a complete
   badge toolkit. The per-model status UI should mirror this (new enum + new map +
   reused CSS/fragment pattern), not introduce new badge CSS or a new polling
   mechanism.

5. **A second connector timed loop.** The connector already runs a recurring
   monotonic-clock tick (`_DETECT_REPORT_INTERVAL` at `agentludum_connector.py:1293`)
   and a single CLI runner (`_run:350`). The ~60s verification tick should be a
   sibling clock-gate next to the PID report, and the test call should reuse `_run`
   (with a low `_call_timeout`), not a new subprocess wrapper or a second loop.

6. **Re-deriving truncation per call site.** The connector already slices `[:300]`
   in eight places; the temptation is to repeat that inline at the new UI/up-channel
   site. Centralize the (cap + path-strip + token-mask) logic in one helper (FR-015)
   so sanitization is consistent and testable, rather than copying a bare slice.

## Bottom line

The biggest wins are **extend, not build**: copy the `report_pid` endpoint shape
(`app/routes/agent_next_turn.py:84`) for both new verification channels and add a
second `_DETECT_REPORT_INTERVAL`-style tick beside the existing one in the
connector (`scripts/agentludum_connector.py:1293`) that reuses the existing `_run`
CLI runner for the cheap test call. The model-readiness status should **mirror**
the proven `ProviderReadiness` / `ConnectionHealth` enum→badge-map→HTMX-fragment
pattern (`provider_readiness.py`, `connection_health_badge.py`) as a new
*axis* rather than a changed enum, and the agent-settings picker is pure surface
work over the already-shipped `Agent.preferred_model` column plus the
`PROVIDER_MODELS` allowlist. Only three things are genuinely new and justified:
the `model_verifications` table (the per-provider `connection_providers` row
physically can't hold per-model state), the two dedicated channel endpoints (the
turn poll/submit bodies provably can't carry this data in the idle/missed-deadline
states), and one small error-sanitization helper (none exists today).
