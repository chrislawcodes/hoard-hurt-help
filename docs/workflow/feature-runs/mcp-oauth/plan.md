# Plan: MCP OAuth — one-click Google sign-in at `/mcp`

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: All three findings are correct and already captured as explicit PLAN-stage decisions in the spec — they are HOW choices a spec intentionally defers, not spec defects: (1) get_game_state public carve-out = open design point 7 (plan must keep it reachable or make it auth-required + add a test); (2) one-canonical-connection uniqueness = FR-006 (plan must add a DB uniqueness constraint on a per-user Mode A marker + transactional upsert/lock; user_id is indexed-not-unique); (3) FR-012 credential mechanism = open design point 1 (plan must CHOOSE encrypted-at-rest vs short-lived internal token vs in-process call, with rotation/expiry semantics). Residual risks (fastmcp mount/lifespan hazard, four-client external risk, provider enablement) map to open design points 3/4, R1, and FR-007/design point 2. No further spec edit — these resolve at the plan, verified at the plan checkpoint.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Same three plan-stage decisions as Codex, plus routing: (1) FR-012/R4 credential trade-offs = open design point 1, plan chooses + states verification; (2) /.well-known + /authorize/token mount correctness = open design point 4 (plan specifies how OAuth/metadata routes mount relative to /mcp and the Railway TLS proxy without colliding with existing API routes); (3) get_game_state public-tool gating = open design point 7. Residual risks map to R1 (four-client), the cost/long-poll note (verify long-poll survives the fastmcp migration — fold into plan verification), and FR-006 (DB unique constraint + lock). No spec edit needed; resolved and verified at the plan stage.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: HIGH (Mode A not live until first tool call blocks first web-join): BINDING tasks requirement — call mark_seen at Mode A connection CREATION (which happens on the client's first call incl. tools/list), so the connection is live the moment the client connects; web-join then works while the client is connected. This mirrors the connector's existing 'must be live/running to join' constraint (web_player.py coverage+capacity gate), not a new flow. Captured in Slice 3 (create→mark_seen) + Slice 4 (connectorless join+play verification). MEDIUM (extraction drops throttles + side effects): BINDING tasks requirement for Slice 2 — the agent_play extraction MUST carry the route-level rate-limit guards (_last_poll/_last_pull, keyed off connection) and post-submit side effects (turns_played increment, mark_first_move) INTO the shared service so the MCP path gets them too; parity test asserts an MCP submit increments turns_played and marks first move. MEDIUM (get_game_state breaking): intentional + documented — AD-5 migration story; tests/test_mcp.py updated to expect auth-required; public reads remain on the HTTP spectator endpoint.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: (1) SQLite partial-index/IntegrityError retry: RR-7 + RR-9 + AD-2 (find-or-create tolerates IntegrityError/locked by re-select; concurrency test on async in-memory SQLite). (2) Gate/heartbeat drift relies on devs calling the helper: mitigated by the single shared assert_connection_usable in deps.py + RR-5/RR-8 parity tests; mark_seen is activity-tracking only (the scheduler drives turns server-side, not off the bridge's last_seen). (3) SQLite vs Postgres partial-index DDL: RR-9, guarded by tests/test_migrations.py (alembic upgrade head on SQLite in CI; DDL reviewed Postgres-valid). (4) agent_play bloat/circular deps: agent_play imports app/models + app/engine only (NOT app/routes), so no circular dependency; Slice 2 unit-tests it directly against the test DB. All terminal — these are implementation-time verifications, captured in tasks.

## Architecture decisions (resolving the spec's open design points)

### AD-1 — Credential mechanism (FR-012) = Option B: shared in-process play-service layer
**The MCP tools no longer call our own HTTP API over the network.** The play
actions the tools use are extracted into a shared service layer that both the
existing agent HTTP routes and the MCP tools call. The MCP adapter authenticates
via OAuth, resolves the user's per-user Connection, and calls the service in-process
with the resolved `Connection`/`Player`. **No internal key is forwarded or stored;
no crypto dependency is added.** This adopts the in-process approach *as* the FR-012
mechanism — exactly what spec open design point 1 permitted and the non-goal's escape
clause allowed. Options A (encrypt-at-rest) and C (fresh per-session key) were
rejected: A cements the loopback + adds `cryptography` + a master secret and is partly
throwaway; C is racy under concurrent clients.

The shared layer lives at **`app/engine/agent_play.py`** (game-agnostic core; name
final at tasks time). Scope is bounded to the operations MCP uses: get-next-turn,
get-turn, submit-talk, submit-action, and the read tools (chat, opponent history,
turn detail, standings). HTTP routes become thin adapters:
`parse → require_connection → require_agent_player → agent_play.<fn>(...)`. The MCP
tool is the other adapter: `OAuth → resolve user → Mode A connection → resolve player
→ agent_play.<fn>(...)`.

**Service-layer signatures (plan-review):** the `agent_play.*` functions take **domain
arguments only** — an `AsyncSession`, the resolved `Connection`/`Player`, and parsed
payload values — **never** a Starlette `Request` or the web session. The HTTP route
extracts those via existing `deps.py` dependencies and passes plain args; the MCP adapter
passes the OAuth-resolved connection/player + tool args. So neither adapter reconstructs
request/session context, and there is one logic implementation (this is what keeps the
"thin adapter" honest and prevents RR-5 drift). Slice 2 proves it: if a handler can't
cleanly separate from `Request`, that surfaces as a failing extraction test there.

### AD-2 — Per-user "Mode A" Connection + uniqueness (FR-006)
A new nullable marker column on `connections` (e.g. `kind`/`origin = "mode_a"`, final
name at tasks time) identifies the one OAuth-owned connection per user. Uniqueness is
enforced by a **partial unique index** on `(user_id)` WHERE the marker is set (so it
does not clash with connector connections, which can be many per user), plus a
**transactional upsert** (insert-or-select inside one transaction; on integrity error,
re-select) so concurrent OAuth callbacks converge on one row. The partial unique index
must be written **Postgres-compatible** (prod) and exercised by a concurrency test on the
**SQLite** in-memory test DB (CI); note any engine difference in the migration (the repo
already handles SQLite/Postgres divergence via batch ops). The connection still
needs the NOT-NULL `key_lookup`/`key_hint`: mint once with the existing
`generate_connection_key` + `bot_key_lookup`/`bot_key_hint`, store the hash, **discard
the raw key** (the in-process path never needs it). Status starts `ACTIVE`.

**Deletion hole (plan-review MEDIUM):** delete is soft (`deleted_at` set, row kept). So
the partial unique predicate must be `WHERE marker set AND deleted_at IS NULL` — a
soft-deleted Mode A row must NOT occupy the slot — and `mode_a_connection_for(user)` must
**resurrect** a soft-deleted row (clear `deleted_at`, re-`ACTIVE`, re-enable providers) or
create fresh, so a user who deleted it can re-sign-in cleanly. **SQLite locking
(plan-review):** the test DB is async in-memory SQLite; the concurrency test exercises
concurrent callers of `mode_a_connection_for`, and the find-or-create must tolerate
`IntegrityError`/`database is locked` by re-selecting (insert→flush in a nested
transaction, on conflict re-select the existing row). On Postgres the partial unique
index + re-select is the source of truth.

### AD-3 — Provider enablement (FR-007)
On Mode A connection creation, enable `connection_providers` rows for **all known
`ConnectionProvider` values** (reusing the upsert helper in
`connections_lifecycle.py`). The user owns all their own agents, so enabling all
providers on their single OAuth connection lets `require_agent_player`'s provider join
resolve any current or future agent without per-agent bookkeeping. Idempotent: re-run
on each connect to backfill any provider added since.

### AD-4 — fastmcp v3 migration (R5)
Migrate `mcp_server/server.py` from `mcp.server.fastmcp` to standalone `fastmcp` v3.
Keep the 9 tools and the `/mcp` mount. The parent app already drives the MCP sub-app
lifespan in `app/main.py:158-172`; confirm the standalone server still exposes a
`streamable_http_app()`-equivalent ASGI app with a lifespan the parent can run.
**This slice ships before OAuth** so the migration is validated in isolation.

### AD-5 — OAuth gate, discovery, and the public-tool decision (FR-001..004, design point 7)
Wrap `/mcp` with fastmcp's `GoogleProvider`/`OAuthProxy` configured from our existing
Google app credentials + a server `base_url` + a JWT signing key. This makes `/mcp` an
OAuth 2.1 Resource Server (401 + `WWW-Authenticate`, RFC 9728 PRM, AS metadata, DCR,
PKCE) with no hand-rolled OAuth. The well-known/`authorize`/`token`/`register` routes
are served by the provider on the MCP app; confirm they sit correctly relative to the
`/mcp` mount and Railway's TLS proxy (DNS-rebinding host check already disabled —
`mcp_server/server.py:32-36`) and that the advertised `base_url` is the public HTTPS
host.

**`get_game_state`: consciously made auth-required** under the OAuth-only gate (the
honest reconciliation of FR-008's "stays public" with the OAuth-only `/mcp` decision).
Truly public game state remains served by the existing **public HTTP spectator
endpoint** (`/api/spectator/matches/{id}/state`) that the tool already proxies, so no
public access is lost — it just isn't via an MCP tool anymore. A test asserts the
chosen behavior so it can't silently flip.

**Migration story (plan-review MEDIUM):** with OAuth-only `/mcp` there is **no per-tool
public exemption** — the provider gates the whole server — so anonymous MCP reads of
`get_game_state` are intentionally removed. `tests/test_mcp.py` (which today asserts
`get_game_state` is in the *public* tool set) is **updated** to assert it now requires
auth. Public reads move to the HTTP spectator endpoint + the spectator web pages
(unchanged). Slice 5 records this in `docs/setup-mcp.md` as the deprecation note for any
caller relying on the old anonymous tool.

### AD-6 — Config + startup checks (FR-013)
Add OAuth/MCP settings to `app/config.py` (reuse the existing
`google_client_id`/`google_client_secret`; add MCP `base_url`, JWT signing key, and any
extra redirect URIs) and extend `_check_oauth_config` in `app/main.py` to fail loud in a
real deployment when the new required vars are missing, warn-but-run in local dev. No
secret committed.

### AD-7 — Re-apply connection gates + heartbeat in the MCP bridge (plan-review HIGH ×2)
**Going in-process (AD-1) bypasses `require_connection`, which is also where the
deleted/paused/disabled gates AND the `mark_seen` heartbeat run.** So the MCP bridge MUST
re-apply them itself or a disabled/paused user would reach `/mcp` and the Mode A
connection would read as stale. Fix without duplication: **extract the gate checks from
`require_connection` into a shared helper in `app/deps.py`** — e.g.
`assert_connection_usable(connection)` raising the same 410/403 envelopes (deleted /
paused / disabled-account) — and call it from **both** `require_connection` and the MCP
bridge. The bridge also calls the existing `mark_seen` (in `app/engine/connection_activity.py`,
**called, not modified**) on each tool call so `last_seen_at`/`api_call_count` stay fresh.
This keeps one implementation of the gates (no drift) and stays inside the scope paths
(`app/deps.py`).

### AD-8 — Mode A connection in `/me/connections` (plan-review MEDIUM, revised)
Codex showed that filtering the Mode A row out of just the list page is **leaky** —
`_load_owned_connection`, the pause/resume/delete routes, and the nav counters all still
see it by ownership. So we **don't** half-hide it. Instead the Mode A connection is a
**real, visible, manageable connection**: pause / resume / delete / nav-counter behavior
all operate on it with **zero new code**, which is what satisfies FR-011 ("operator can
pause/stop an OAuth player"). The only rough edge is that its key-paste **setup/rotate**
instructions are meaningless (it has a `key_hint` but the user never received a raw key) —
cleanly relabeling that row as an "AI sign-in" connection lives in
`app/templates/connections/*`, which is **out of the current scope paths**, so it is a
small, tracked **follow-up**. For v1 the row renders as a generic connection; nothing
breaks. (Rotate would mint a new key the OAuth path ignores — harmless.)

### AD-9 — Scope: OAuth sign-in is a full path — connectorless create + play (operator decision: X)
**Confirmed:** signing in via an AI client lets a user create agents and play with **no
connector at all**. This is the system's *natural* behavior, confirmed in code:
`connection_health._connection_is_live` (and `provider_is_covered` /
`live_provider_capacity` / `agents_setup._enabled_provider_values`) treats a connection
as live/covering when `last_seen_at` is within `LIVE_WINDOW_SECONDS` (90s) and it is
not-paused / not-deleted — **`runner_pid` is NOT required**. So the Mode A connection
(all providers enabled per AD-3 + `mark_seen` on each tool call per AD-7) naturally reads
as covered → unlocks agent creation + play. We **do not** add suppression code; we
**add tests** for the connectorless create→play flow (`connection_health.py` /
`agents_setup.py` are not modified — their existing behavior is what we rely on, so no
scope expansion). This supersedes the earlier narrow framing and the spec's
"agent creation stays on the web" edge case is relaxed accordingly.

**Existing web onboarding still applies (plan-review MEDIUM):** "connectorless" removes
the *connector* requirement, **not** the normal web steps. A first-time OAuth user still
signs in, **picks a handle** (`require_user_with_handle` redirects handle-less users to
`/me/handle`), and **creates agents on the web** — MCP-driven agent creation stays a
non-goal. So the full path is: sign in → pick handle → create agent on the web → connect
the AI client via OAuth → play. AD-9 changes only the "must run a connector" gate.

**Known-safe interaction (turn routing):** if a user runs the connector **and** is signed
in via OAuth at the same time, both connections are eligible for the same agents.
`turn_routing.py`'s atomic sticky-pin claim guarantees no double-serve; in practice the
connector tends to win pins and the MCP `get_next_turn` returns `waiting` for those
turns — acceptable. *verification:* a test that two eligible connections for one user
never both serve the same turn (pin is exclusive).

## Reuse decisions (from reuse-report.md — every row addressed)

- **REUSE**: `sync_google_user` (identity by Google `sub`); `GoogleUserInfo`;
  authlib Google client; `User.google_sub`; `generate_connection_key` +
  `bot_key_lookup`/`bot_key_hint` (to populate the Mode A connection's key columns);
  `require_connection` lifecycle gates (deleted/paused/disabled) — the Mode A
  connection benefits from them unchanged; the `/mcp` mount + lifespan driving.
- **EXTEND**: the MCP server (migrate to fastmcp v3); the `connection_providers`
  upsert helper (`connections_lifecycle.py`); OAuth settings + `_check_oauth_config`.
- **JUSTIFIED-NEW**: the OAuth Resource-Server/PRM/AS-metadata/DCR/PKCE stack (comes
  from fastmcp, not hand-rolled); the OAuth→Mode A Connection bridge; the per-user
  uniqueness index + transactional upsert; the shared `agent_play.py` service layer
  (extraction, not duplication). **No encryption-at-rest helper and no JWT-key store
  are needed under AD-1** — this removes two would-be new modules the reuse audit
  flagged as gaps (the JWT signing key is fastmcp config, not a new subsystem).

## Wave / slice breakdown (each slice ≤ ~300 changed lines; `[CHECKPOINT]` at boundaries)

1. **Slice 1 — fastmcp v3 migration (no behavior change).** Swap imports/transport;
   keep 9 tools + header auth temporarily; keep mount + lifespan. `[CHECKPOINT]`
   *verification:* `/mcp` smoke test — tools list + one header-authed call succeed
   exactly as today; preflight green.
2. **Slice 2 — extract `app/engine/agent_play.py`.** Move the play logic out of
   `agent_api.py`/`agent_next_turn.py` into service functions; routes call them; no
   signature/behavior change at the HTTP boundary. `[CHECKPOINT]` *verification:*
   existing agent-API + connector tests pass unchanged; new direct unit tests on the
   service functions.
3. **Slice 3 — shared connection gates + Mode A connection model + bridge helper.**
   Extract `assert_connection_usable()` in `app/deps.py` from `require_connection` and
   wire `require_connection` to call it (no behavior change for the existing path — its
   tests must stay green). Migration: marker column + Postgres-compatible partial unique
   index; `mode_a_connection_for(user)` find-or-create (transactional upsert,
   all-providers enablement via the `connection_providers` upsert helper, key minting
   then raw-key discard). `[CHECKPOINT]` *verification:* concurrency test — N parallel
   calls for one user yield exactly one connection on SQLite (SC-004);
   `assert_connection_usable` raises the right 410/403 for deleted/paused/disabled.
4. **Slice 4 — OAuth gate + MCP tools call the service in-process.** Configure
   `GoogleProvider`; make `/mcp` OAuth-only; tools resolve token→user→Mode A
   connection→`assert_connection_usable` (gates re-checked **every call** with a
   freshly-loaded user, so a mid-session disable bites next call)→`mark_seen`→player→
   `agent_play.<fn>`; `get_game_state` made auth-required + test. `[CHECKPOINT]`
   *verification:* `curl -i /mcp` → 401 + `WWW-Authenticate` + fetchable PRM/AS metadata
   (R2); a token test for audience rejection; a disabled-user token is rejected at `/mcp`;
   **connectorless flow** — a user whose only connection is the Mode A row reads as
   covered and can resolve + play a turn (AD-9); pausing/deleting the Mode A connection
   stops `/mcp` (FR-011); connector regression green.
5. **Slice 5 — config, startup checks, docs.** `app/config.py` + `_check_oauth_config`;
   rewrite `docs/setup-mcp.md` per-client OAuth connect snippets (Claude Code/Desktop,
   Codex, Gemini CLI), add a short migration note that the `get_game_state` MCP tool now
   needs auth (public state is the spectator HTTP endpoint), and fix the
   `X-Agent-Key`/`sk_bot_` → `X-Connection-Key`/`sk_conn_` drift. `[CHECKPOINT]`
   *verification:* preflight green; docs reviewed.
6. **Slice 6 — cross-client live validation (operator, manual).** Run the real OAuth
   connect on all four clients. *verification:* each completes OAuth + one tool call,
   recorded; any client that cannot do spec OAuth → `block` to operator (R1).

## Residual risks (each with a verification action — required to advance)

- **RR-1 — A client can't complete spec OAuth (DCR/PKCE).** No `/mcp` header fallback.
  *verification:* Slice 6 runs the live connect on each of the four clients early;
  a failing client is escalated via `block` for an operator decision (ship-without /
  wait / documented connector path), not worked around in code.
- **RR-2 — Discovery/metadata base-URL mismatch behind Railway's TLS proxy.**
  *verification:* in Slice 4, `curl -i https://<prod-host>/mcp` and fetch the advertised
  PRM + AS metadata; confirm every advertised URL is the public HTTPS host and resolves
  (no redirect loop / Invalid Host) before any client validation.
- **RR-3 — fastmcp migration breaks the mount or a tool.** *verification:* Slice 1's
  `/mcp` smoke test (tools list + one call) must pass before any OAuth work begins.
- **RR-4 — Long-poll / idle cost regresses under fastmcp v3.** `get_next_turn` long-poll
  is the cheap-idle property. *verification:* after Slice 4, confirm an idle
  `get_next_turn` still returns a `waiting` status with `next_poll_after_seconds` and
  does not busy-loop; compare wall-time of one idle cycle against the pre-migration path.
- **RR-5 — Two paths drift** (HTTP vs MCP) once both call `agent_play.py`.
  *verification:* both adapters exercised by tests against the same service functions;
  a test asserts an MCP submit and an HTTP submit produce identical `TurnSubmission`
  rows for equivalent inputs.
- **RR-6 — Mode A connection's discarded raw key.** Minting then discarding the raw key
  means the row's key can't be used by the connector. *verification:* confirm nothing in
  the connector/UI assumes the Mode A connection has a usable raw key; the connections
  dashboard renders it without error (a test or manual check on the rendered row).
- **RR-7 — SQLite `database is locked` under concurrent Mode A upsert.** *verification:*
  the AD-2 concurrency test runs N concurrent `mode_a_connection_for` calls on the async
  in-memory SQLite test DB and asserts exactly one row with no unhandled lock error
  (the find-or-create retries on `IntegrityError`/locked by re-selecting).
- **RR-8 — Dual-auth user-resolution drift.** The HTTP path resolves the user via the
  connection key; the MCP path resolves the user via the OAuth token → Google `sub`.
  *verification:* a test that both resolve to the **same** `User` row for the same person
  (the MCP bridge uses `sync_google_user`, not a parallel lookup), and that
  `assert_connection_usable` applies identical gates on both paths.
- **RR-9 — Alembic partial-unique-index divergence (SQLite vs Postgres).** Past
  migrations (`0028`/`0029`) show engine-specific friction. *verification:* `alembic
  upgrade head` succeeds on the SQLite dev/test DB in CI **and** the index DDL is
  Postgres-valid (reviewed against the prod engine); guarded by `tests/test_migrations.py`.

## Out of scope (carried from spec non-goals)
Connector auth changes; removing the `sk_conn_`/`require_connection` HTTP path;
refactoring `require_connection` to take OAuth identity (Option B-of-discovery, rejected);
Cursor; MCP-driven new-user agent creation. The in-process refactor is **in** scope here
only as the bounded AD-1 mechanism (the MCP-used operations), not an app-wide rewrite.
