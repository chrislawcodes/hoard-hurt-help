# Plan: MCP OAuth — one-click Google sign-in at `/mcp`

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: All three findings are correct and already captured as explicit PLAN-stage decisions in the spec — they are HOW choices a spec intentionally defers, not spec defects: (1) get_game_state public carve-out = open design point 7 (plan must keep it reachable or make it auth-required + add a test); (2) one-canonical-connection uniqueness = FR-006 (plan must add a DB uniqueness constraint on a per-user Mode A marker + transactional upsert/lock; user_id is indexed-not-unique); (3) FR-012 credential mechanism = open design point 1 (plan must CHOOSE encrypted-at-rest vs short-lived internal token vs in-process call, with rotation/expiry semantics). Residual risks (fastmcp mount/lifespan hazard, four-client external risk, provider enablement) map to open design points 3/4, R1, and FR-007/design point 2. No further spec edit — these resolve at the plan, verified at the plan checkpoint.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Same three plan-stage decisions as Codex, plus routing: (1) FR-012/R4 credential trade-offs = open design point 1, plan chooses + states verification; (2) /.well-known + /authorize/token mount correctness = open design point 4 (plan specifies how OAuth/metadata routes mount relative to /mcp and the Railway TLS proxy without colliding with existing API routes); (3) get_game_state public-tool gating = open design point 7. Residual risks map to R1 (four-client), the cost/long-poll note (verify long-poll survives the fastmcp migration — fold into plan verification), and FR-006 (DB unique constraint + lock). No spec edit needed; resolved and verified at the plan stage.

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

### AD-2 — Per-user "Mode A" Connection + uniqueness (FR-006)
A new nullable marker column on `connections` (e.g. `kind`/`origin = "mode_a"`, final
name at tasks time) identifies the one OAuth-owned connection per user. Uniqueness is
enforced by a **partial unique index** on `(user_id)` WHERE the marker is set (so it
does not clash with connector connections, which can be many per user), plus a
**transactional upsert** (insert-or-select inside one transaction; on integrity error,
re-select) so concurrent OAuth callbacks converge on one row. The connection still
needs the NOT-NULL `key_lookup`/`key_hint`: mint once with the existing
`generate_connection_key` + `bot_key_lookup`/`bot_key_hint`, store the hash, **discard
the raw key** (the in-process path never needs it). Status starts `ACTIVE`.

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

### AD-6 — Config + startup checks (FR-013)
Add OAuth/MCP settings to `app/config.py` (reuse the existing
`google_client_id`/`google_client_secret`; add MCP `base_url`, JWT signing key, and any
extra redirect URIs) and extend `_check_oauth_config` in `app/main.py` to fail loud in a
real deployment when the new required vars are missing, warn-but-run in local dev. No
secret committed.

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
3. **Slice 3 — Mode A connection model + bridge helper.** Migration: marker column +
   partial unique index; `mode_a_connection_for(user)` find-or-create (transactional
   upsert, all-providers enablement, key minting). `[CHECKPOINT]` *verification:*
   concurrency test — N parallel calls for one user yield exactly one connection
   (SC-004); paused/deleted/disabled semantics covered.
4. **Slice 4 — OAuth gate + MCP tools call the service in-process.** Configure
   `GoogleProvider`; make `/mcp` OAuth-only; tools resolve token→user→Mode A
   connection→player→`agent_play.<fn>`; `get_game_state` auth-required carve-out
   decision + test. `[CHECKPOINT]` *verification:* `curl -i /mcp` → 401 +
   `WWW-Authenticate` + fetchable PRM/AS metadata (R2); a token test for audience
   rejection; connector regression still green.
5. **Slice 5 — config, startup checks, docs.** `app/config.py` + `_check_oauth_config`;
   rewrite `docs/setup-mcp.md` per-client OAuth connect snippets (Claude Code/Desktop,
   Codex, Gemini CLI) and fix the `X-Agent-Key`/`sk_bot_` → `X-Connection-Key`/`sk_conn_`
   drift. `[CHECKPOINT]` *verification:* preflight green; docs reviewed.
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
  dashboard renders it correctly (a test or manual check on the rendered row).

## Out of scope (carried from spec non-goals)
Connector auth changes; removing the `sk_conn_`/`require_connection` HTTP path;
refactoring `require_connection` to take OAuth identity (Option B-of-discovery, rejected);
Cursor; MCP-driven new-user agent creation. The in-process refactor is **in** scope here
only as the bounded AD-1 mechanism (the MCP-used operations), not an app-wide rewrite.
