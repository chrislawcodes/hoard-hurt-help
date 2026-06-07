# Tasks — 015 Connection/Agent Split (FF pick-up, slices 3–5)

Slices 0–2 are committed (models, auth+turn-resolution, bots-as-agents). These
tasks cover the remainder, split into checkpoint-bounded slices ≤ ~300 lines.
Each slice ends `[CHECKPOINT]`; the diff checkpoint runs Codex+Gemini review.
Constitution gate per slice: ruff + mypy on changed files; full preflight at CP5.

Reuse: see `reuse-report.md` — every route is a rename/extend of an existing
`bots_*` module; no parallel systems.

---

## CP1 — Connections management (/me/connections)

- [ ] T1 [app/routes/connections_setup.py] `/me/connections` list + create (pick provider → **pending** Connection + setup message + poll-for-connect; resume an abandoned pending) + detail (health, agents it powers, runner setup message using `agentludum_connector.py` + `sk_conn_` + `X-Connection-Key`; **no key fingerprint shown**, drop MCP-direct section).
- [ ] T2 [app/engine/connection_health.py] compute live/stalled/ready from the Connection across its agents (heartbeat, stall_threshold, paused) — extends `connection_activity.py` health (FR-024). Connection display: nickname (defaults to provider) · PID-when-live, no key.
- [ ] T3 [app/routes/connections_credentials.py] reissue (graceful overlap) / revoke key — repoint the existing bots_credentials logic to Connection.
- [ ] T4 [app/routes/connections_lifecycle.py] pause/resume; **delete = DETACH** its agents (kept, paused, needs-connection) + warning confirm; **reattach** an agent to a same-provider connection (FR-029).
- [ ] T5 [app/engine/pending_connection_gc.py or scheduler hook] GC `pending` connections > 24h (FR-024).
- [ ] T6 [app/templates/connections/list.html, detail.html, _health_badge.html, _reconnect.html] connection templates (from bots/ split).
- [ ] T7 [tests/test_connection_management.py] reissue overlap; delete detaches (agent survives, reattachable); pending GC; health across agents.

- CP1 complete [CHECKPOINT]
  (ruff+mypy on changed files; test_connection_management.py green.)

---

## CP2 — Agents management (/me/agents + /me/agents/new + versioning)

- [ ] T8 [app/routes/agents_setup.py] `/me/agents` list (clean, `[+ New agent]` button) + **dedicated `/me/agents/new`** combined create page (use existing connection OR connect a new AI inline → name → model (validated vs PROVIDER_MODELS) → strategy → creates Agent + AgentVersion v1) + agent detail with version history.
- [ ] T9 [app/routes/agents_lifecycle.py] rename/pause/delete agent; set-model/strategy → **update current version if unfrozen, else fork v N+1**; freeze a version at its first rated-match start; block edit while a version is mid-match (FR-011).
- [ ] T10 [app/routes/agents_status.py] agent onboarding/status fragment (from bots_status).
- [ ] T11 [app/templates/agents/list.html, detail.html, new.html, _status.html, _versions.html] agent templates: combined create page, state-driven detail, **version-history panel** (numbered + timestamped + per-version rank).
- [ ] T12 [tests/test_agent_versions.py] combined create incl. pending/abandon; model validated vs PROVIDER_MODELS; **fork-on-edit-after-play + draft-edit-in-place + completed match keeps its version**; max_concurrent at join; seat_name uniqueness for two users sharing a name.

- CP2 complete [CHECKPOINT]
  (ruff+mypy on changed files; test_agent_versions.py green.)

---

## CP3 — Migrate remaining web routes + retire bots_*

- [ ] T13 [app/routes/web_player.py] join records agent_id + agent_version_id; **enforce max_concurrent_games** at join (FR-022); `seat_name = handle/name` uniquified per match (FR-013).
- [ ] T14 [app/routes/web_viewer.py, app/routes/web_support.py, app/routes/web_lobby.py, app/routes/admin_web.py, app/routes/nav_context.py, app/routes/auth.py] migrate Bot→Connection/Agent references; **public identity = seat_name** everywhere the protocol exposed the string id; **two nav entries** (Connections, Agents).
- [ ] T15 [app/routes/bots_setup.py, bots_lifecycle.py, bots_status.py, bots_credentials.py, bots_web_support.py, bots_web.py, app/templates/bots/] DELETE superseded bots routes/templates; update router registration in app/routes/web.py + app/main.py.
- [ ] T16 verify `python3 -c "import app.main"` succeeds.

- CP3 complete [CHECKPOINT]
  (app imports; ruff+mypy on changed files.)

---

## CP4 — Runner + MCP

- [ ] T17 [scripts/agentludum_agent.py → scripts/agentludum_connector.py] **rename** (served at `/runners/agentludum_connector.py`); key by connection (`--key sk_conn_…`, `X-Connection-Key`); read agent_id/agent_name/model/version_no from each next-turn payload; one session per (agent, match) with that agent's model.
- [ ] T18 [mcp_server/server.py] header/key naming `X-Connection-Key`; tools proxy the same agent API; no MCP-direct path.
- [ ] T19 [tests/test_runner_payload.py] runner per-agent model/session selection against a mocked next-turn payload (mock the model CLIs — no live calls).

- CP4 complete [CHECKPOINT]
  (ruff+mypy on changed files; runner test green.)

---

## CP5 — Rename sweep + full preflight green + deliver

- [ ] T20 sweep: `grep -rin "bot" app/ mcp_server/ scripts/ app/templates/`; fix residual symbols/copy so a user's AI player is never "bot"; "bot" labels only scripted opponents. Sweep `connection_activity.py` internal bot naming, `app/engine/sims/` docstrings.
- [ ] T21 confirm no `/me/bots` route, no `Bot` model class; nav = Connections + Agents only.
- [ ] T22 update MEMORY/DESIGN.md/UI.md cross-refs to the new vocabulary if stale (note: ARCHITECTURE.md + DESIGN.md already updated).
- [ ] T23 **full preflight** from repo root: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` — all green, no suppressions.
- [ ] T24 manual quickstart pass for US1/US5/US6/US7 if feasible.

- CP5 complete [CHECKPOINT]
  (full preflight green; ready for deliver.)
