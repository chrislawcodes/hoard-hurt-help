# Tasks: MCP OAuth — one-click Google sign-in at `/mcp`

Source of truth: `spec.md` + `plan.md` (architecture decisions AD-1..AD-9, residual risks
RR-1..RR-9). Slices are a **dependency chain** — see "Parallelization" at the end.
Each slice ends at a single-line `[CHECKPOINT]` marker (≤ ~300 changed lines); the diff
checkpoint reviews only that slice's diff. Preflight (`ruff` + `mypy app/ mcp_server/` +
`pytest`) must be green before advancing any slice.

---

## Slice 1 — fastmcp v3 migration, no behavior change (AD-4, RR-3, RR-4)
Est. diff: ~120 lines. Depends on: none. **DONE** (`cd5b190`).

- [x] Add standalone `fastmcp` v3 to `pyproject.toml`; pin the version; resolve alongside `mcp`.
- [x] Migrate `mcp_server/server.py` to standalone `fastmcp` (`http_app(path="/", transport="streamable-http")`); keep all 9 tools + the `/mcp` mount + lifespan driving in `app/main.py`.
- [x] Keep `X-Connection-Key` header auth working **temporarily** (OAuth lands in Slice 4).
- [x] Verify (RR-3/RR-4): tools list returns 9 tools; header-authed call works; idle `get_next_turn` returns `waiting`; no host-validation regression (fastmcp v3 has none). Preflight green (758 passed).
- [x] Slice 1 complete — preflight green, committed. [CHECKPOINT]

---

## Slice 2 — extract the shared play-service layer (AD-1, RR-5; plan-review MEDIUM)
Est. diff: ~280 lines. Depends on: Slice 1.

- [ ] Create `app/engine/agent_play.py`. Move the core logic of the agent play operations
      (next-turn, get-turn, submit-talk, submit-action, chat, opponent-history, turn-detail,
      standings) out of `app/routes/agent_api.py` + `app/routes/agent_next_turn.py` into
      service functions taking **domain args only** (`AsyncSession`, resolved
      `Connection`/`Player`, parsed payload) — never `Request`/session. Imports `app/models`
      + `app/engine` only (NOT `app/routes`) → no circular dependency.
- [ ] **Carry the route-level guards + side effects into the service** (plan-review MEDIUM):
      the `_last_poll` / `_last_pull` rate-limits (keyed off connection), the `turns_played`
      increment, and `mark_first_move()` after a real submission must live in the shared
      service so both adapters get them.
- [ ] Rewrite the HTTP routes as thin adapters that call the service (auth + parse via
      existing `deps.py`, then delegate). No change to the HTTP request/response contract.
- [ ] Verify: existing agent-API + connector tests pass **unchanged**; add direct unit tests
      on `agent_play.*` (incl. a rate-limit hit and a submit that increments `turns_played` +
      marks first move).
- [ ] Slice 2 complete — preflight green, commit. [CHECKPOINT]

---

## Slice 3 — shared connection gates + Mode A connection model (AD-2, AD-3, AD-7; RR-7/8/9)
Est. diff: ~280 lines. Depends on: Slice 2.

- [ ] Extract `assert_connection_usable(connection)` in `app/deps.py` from
      `require_connection` (deleted→410, paused→403, disabled-account→403); wire
      `require_connection` to call it — **no behavior change** for the existing path.
- [ ] Migration: add the nullable Mode A marker column to `connections` + a
      **Postgres-compatible partial unique index** on `(user_id)` `WHERE marker set AND
      deleted_at IS NULL`. Use `op.batch_alter_table` where needed for SQLite (see
      `tests/test_migrations.py`).
- [ ] Add `mode_a_connection_for(user)` (find-or-create): transactional upsert tolerant of
      `IntegrityError`/`database is locked` (insert→flush, on conflict re-select);
      **resurrect** a soft-deleted row (clear `deleted_at`, set `ACTIVE`, re-enable
      providers); mint key via `generate_connection_key` + `bot_key_lookup`/`bot_key_hint`,
      store hash, discard raw key; enable **all** `ConnectionProvider` rows (reuse the
      `connections_lifecycle.py` upsert helper); **call `mark_seen` on creation** so the row
      is live immediately (plan-review HIGH).
- [ ] Verify (SC-004/RR-7/RR-9): N concurrent `mode_a_connection_for` calls → exactly one row,
      no unhandled lock error (SQLite async test DB); `assert_connection_usable` raises the
      right 410/403; resurrect-after-delete works; `alembic upgrade head` green; parity test
      that `require_connection` still gates identically.
- [ ] Slice 3 complete — preflight green, commit. [CHECKPOINT]

---

## Slice 4 — OAuth gate + MCP tools call the service in-process (AD-5, AD-7, AD-9; RR-1/2/8)
Est. diff: ~300 lines. Depends on: Slices 1–3.

- [ ] Configure fastmcp `GoogleProvider`/`OAuthProxy` on `/mcp` from the existing Google
      app creds + server `base_url` + JWT signing key; `/mcp` becomes OAuth-only (401 +
      `WWW-Authenticate` + RFC 9728 PRM + AS metadata + DCR + PKCE, all provider-supplied).
- [ ] Replace `_connection_key_from_ctx` usage in the tools with: read verified token →
      resolve `User` via `sync_google_user` (Google `sub`) → `mode_a_connection_for(user)` →
      `assert_connection_usable` (re-checked **every call**, user loaded fresh) → `mark_seen`
      → resolve player → call `agent_play.<fn>` in-process.
- [ ] `get_game_state` becomes auth-required; update `tests/test_mcp.py` to expect that
      (no per-tool public exemption under the gate).
- [ ] Verify (R1/RR-2/RR-8): `curl -i /mcp` (no token) → 401 + `WWW-Authenticate`; PRM + AS
      metadata fetch and advertise the public host (no Invalid Host / redirect loop);
      wrong-audience token rejected; disabled-user token rejected; **connectorless flow** — a
      user whose only connection is the Mode A row reads as covered, can join + play a turn;
      pausing/deleting the Mode A connection stops `/mcp` (FR-011); connector regression green;
      HTTP-vs-MCP parity (same `TurnSubmission` for equivalent inputs).
- [ ] Slice 4 complete — preflight green, commit. [CHECKPOINT]

---

## Slice 5 — config, startup checks, docs (AD-6, FR-013)
Est. diff: ~150 lines (+docs). Depends on: Slice 4.

- [ ] Add OAuth/MCP settings to `app/config.py` (reuse `google_client_id`/`google_client_secret`;
      add MCP `base_url`, JWT signing key, extra redirect URIs). No secret committed.
- [ ] Extend `_check_oauth_config` in `app/main.py` to fail loud in a real deployment when
      the new required vars are missing; warn-but-run in local dev.
- [ ] **Close the Slice-4 HIGH finding:** `_check_oauth_config` MUST also require the new MCP
      OAuth vars (base_url + JWT signing key, alongside the Google client id/secret) and exit
      before serving when `RAILWAY_ENVIRONMENT_ID` is set — so the `dev-google-client-id`
      placeholder fallback in `mcp_server/server.py::_build_auth_provider` can NEVER run in a
      real deployment (it stays a local-dev-only convenience). Add a test asserting prod-missing
      vars raises at startup.
- [ ] Rewrite `docs/setup-mcp.md`: per-client **OAuth** connect snippets for Claude Code,
      Claude Desktop, Codex, Gemini CLI; add the `get_game_state` deprecation note (now
      auth-required; public reads via the spectator endpoint); **fix the doc drift**
      (`X-Agent-Key`/`sk_bot_` → `X-Connection-Key`/`sk_conn_`).
- [ ] Verify: preflight green; docs reviewed.
- [ ] Slice 5 complete — preflight green, commit. [CHECKPOINT]

---

## Slice 6 — cross-client live validation (operator, manual; RR-1, SC-001)
No code diff. Depends on: Slices 4–5 deployed to a reachable host with the Google app's
new redirect URIs registered.

- [ ] Operator runs the real OAuth connect on **Claude Code**, **Claude Desktop**, **Codex**,
      **Gemini CLI**: each completes Google consent and at least one tool call.
- [ ] Record results in the run's validation notes. Any client that cannot complete spec
      OAuth (DCR/PKCE) → `block --slug mcp-oauth --reason "<client> cannot complete spec
      OAuth: <detail>"` for an operator decision (all-four gate per discovery Q4).

---

## Parallelization
The slices form a **dependency chain** (1→2→3→4→5; 6 needs a deploy). No safe parallel
split: Slice 4 depends on 1+2+3, and Slice 5's config is exercised by Slice 4. Slice 5's
**docs** could be drafted alongside Slice 4 but ship together to avoid a half-documented
state. Recommendation: run sequentially; no `[P:]` annotations.

## Follow-ups (tracked, out of this run)
- Relabel/hide the Mode A row's key-paste & rotate UI in `app/templates/connections/*`
  (templates are out of the current scope paths) — AD-8.
- Full connector-less onboarding polish (e.g. surfacing "AI sign-in connections" as a
  first-class managed surface).
