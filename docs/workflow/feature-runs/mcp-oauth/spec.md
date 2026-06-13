# Spec: MCP OAuth — one-click Google sign-in to connect an AI client to `/mcp`

**Slug:** mcp-oauth
**Branch:** feat/mcp-oauth (off origin/main)
**Status:** draft (spec stage)
**Scope paths:** `mcp_server/`, `app/deps.py`, `app/routes/`, `app/main.py`, `pyproject.toml`

## Background

Today a player connects an AI client to our MCP server by pasting a long-lived
secret. Every MCP tool reads an `X-Connection-Key` header off the request and
forwards it to our internal HTTP API:

- `mcp_server/server.py` builds the server with the SDK-bundled FastMCP
  (`from mcp.server.fastmcp import FastMCP`, dependency `mcp>=1.0.0`). It exposes
  9 `@mcp_app.tool()` tools; each authenticated tool calls
  `_connection_key_from_ctx(ctx)` to pull the `X-Connection-Key` header and
  forwards it as a header to `/api/...` (`_headers`). The server is mounted at
  `/mcp` via `mcp_app.streamable_http_app()` (`app/main.py:222`).
- `app/deps.py:require_connection` validates the `sk_conn_…` key (hash lookup via
  `app.engine.tokens.bot_key_lookup`) and returns a `Connection`. All agent play
  (next-turn, message, submit, history, standings) authenticates this way.
- `scripts/agentludum_connector.py` is a separate always-on runner that hits the
  **HTTP API directly** with the raw `sk_conn_` key. It never talks to `/mcp`.

Pasting a secret into client config is fragile and unsafe: it's a long-lived
credential the user copies by hand, it can leak through chat or logs, and it has
no consent step. We already are an OAuth **client** of Google for human login
(`app/routes/auth.py`, `sync_google_user`, `app.auth.google.oauth`), so the user
is already identified by Google — a head start.

This feature replaces the paste step at `/mcp` with a true one-click, secure
OAuth flow so the user authorizes their AI client with "Sign in with Google" and
never copies a secret.

## What the MCP spec requires (grounding)

A remote HTTP MCP server is an **OAuth 2.1 Resource Server**. To be spec-compliant
and discoverable by real clients (Claude Code/Desktop, etc.), `/mcp` must:

- Return **HTTP 401** with a `WWW-Authenticate: Bearer resource_metadata="…"`
  header on an unauthenticated request.
- Serve **RFC 9728 Protected Resource Metadata** at
  `/.well-known/oauth-protected-resource`, naming the authorization server.
- Expose **Authorization Server metadata** (RFC 8414 / OIDC discovery) with
  `/authorize`, `/token`, `/register`.
- Support **PKCE** (OAuth 2.1) and **audience binding** (reject tokens not minted
  for this server).
- Provide a **Dynamic Client Registration** path (RFC 7591) — today's shipping
  clients expect DCR. **Google does not support DCR**, so a proxy/bridge is
  mandatory: it acts as the authorization server *to the MCP client* and as an
  OAuth client *to Google upstream*. The MCP client ends up holding a token
  minted by **our** server, not Google's.

## Design decisions already made (discovery)

These were settled in discovery and are not reopened by this spec.

1. **Framework: standalone `fastmcp` v3 `GoogleProvider` (built on `OAuthProxy`).**
   It ships the PRM doc, AS metadata, DCR shim, PKCE, and Google token bridging
   out of the box, reusing our existing Google app credentials. Cost: migrate the
   server from the SDK-bundled FastMCP (`mcp.server.fastmcp`) to standalone
   `fastmcp` (mostly an import change plus a few API/transport renames), and we
   own a JWT signing key.
2. **Bridge (the load-bearing decision): OAuth establishes a per-user "Mode A"
   Connection (Option A).** After OAuth, the `/mcp` layer resolves the verified
   Google identity → finds or creates **one canonical Connection** for that user →
   uses that connection's credential to call the internal HTTP API. `require_connection`,
   the play API, and the connector are **unchanged**. This works because
   `require_agent_player` already resolves players by `Agent.user_id`, not by
   connection pinning, so one connection can act for all of a user's agents.
   Option B (refactor `require_connection` to accept OAuth identity directly) was
   **rejected** — it fragments the "playing session = Connection" model and edits
   the most security-sensitive shared auth path.
3. **`/mcp` becomes OAuth-only.** The `X-Connection-Key` header path is dropped at
   `/mcp`; `/mcp` clients connect via OAuth. The raw `sk_conn_` HTTP API
   (`require_connection`) remains as the non-`/mcp` fallback for the connector and
   any direct-API user. This is also the simpler implementation: a FastMCP OAuth
   provider gates *all* `/mcp` traffic by default.
4. **All four clients gate shipping.** Claude Code, Claude Desktop, Codex, and
   Gemini CLI must each complete a real end-to-end OAuth connect before the
   feature is "done." (Risk: client MCP-OAuth maturity is outside our control and
   there is no header fallback at `/mcp` — see Risks.)

## Goal

Let a player connect their AI client to `/mcp` with one click via Google OAuth —
no pasted key, no secret in the URL, no key in chat — and play a full turn,
across Claude Code, Claude Desktop, Codex, and Gemini CLI.

## User Scenarios & Testing

### User Story 1 — One-click OAuth connect and play (Priority: P1)

As a player who already signs in with Google, I add the Hoard-Hurt-Help MCP
server to my AI client and authorize it with a single Google sign-in, then my AI
plays my agents' turns — without me ever copying a secret.

**Why this priority:** This is the feature. Without it there is nothing.

**Independent Test:** From a clean client, add `https://<host>/mcp`, trigger the
OAuth flow (browser opens, Google consent, redirect back), then ask the client to
list tools and call `get_next_turn`. It returns the user's turn (or `waiting`)
with no key configured anywhere.

**Acceptance Scenarios:**

1. **Given** a signed-out client with no key configured, **When** the user adds
   `/mcp` and starts the client, **Then** the client discovers the OAuth flow
   (401 → PRM → AS metadata → DCR/PKCE), opens Google consent, and on approval the
   tools become callable.
2. **Given** an authorized client, **When** the AI calls `get_next_turn`, **Then**
   it acts for the signed-in user's agents and returns the most urgent turn (or a
   `waiting` status), identical in shape to today's header-authed result.
3. **Given** an authorized client, **When** the AI calls `submit_talk` /
   `submit_action` with a valid turn token, **Then** the move is accepted exactly
   as it is for a `sk_conn_`-authed connection.

### User Story 2 — Spec-compliant, secure discovery (Priority: P1)

As an MCP client (or a security reviewer), I expect `/mcp` to behave as a
compliant OAuth 2.1 Resource Server so connecting "just works" and no long-lived
secret is exposed.

**Why this priority:** A spec mismatch is painful to fix later and is the stated
top risk; without correct discovery, clients can't connect at all.

**Independent Test:** `curl -i https://<host>/mcp` (no token) returns `401` with a
`WWW-Authenticate` header; the advertised `/.well-known/oauth-protected-resource`
and AS metadata documents fetch successfully and point at our `/authorize`,
`/token`, `/register`.

**Acceptance Scenarios:**

1. **Given** no `Authorization` header, **When** a client hits `/mcp`, **Then** it
   gets `401` + `WWW-Authenticate` naming the protected-resource metadata URL.
2. **Given** the discovery docs, **When** a client reads them, **Then** it finds a
   working DCR endpoint, `/authorize`, `/token`, and PKCE support.
3. **Given** a token minted for a *different* audience, **When** presented to
   `/mcp`, **Then** it is rejected.
4. **Given** the OAuth flow, **When** it completes, **Then** no `sk_conn_` secret
   and no token ever appears in a URL the user sees or in the chat transcript.

### User Story 3 — Connector and direct API keep working (Priority: P1)

As an existing player running the connector, I keep playing with zero changes
after OAuth ships.

**Why this priority:** Regression protection on the hot path is non-negotiable;
the connector is the recommended (cheaper) play path.

**Independent Test:** With the connector running on its existing `sk_conn_` key,
confirm it still authenticates, gets turns, and submits — no code or config
change.

**Acceptance Scenarios:**

1. **Given** the connector's `sk_conn_` key, **When** it calls the HTTP API,
   **Then** `require_connection` authenticates it unchanged.
2. **Given** a direct HTTP API caller using `X-Connection-Key`, **When** it calls
   `/api/...`, **Then** it works exactly as today.

### User Story 4 — Cross-client validation (Priority: P2)

As the operator, I validate the OAuth connect end-to-end on each supported client
(Claude Code, Claude Desktop, Codex, Gemini CLI) before shipping.

**Why this priority:** The decision is "all four gate shipping," and client
maturity must be checked against real clients, early — not at the end.

**Independent Test:** A documented manual checklist run per client, recorded in
the run's validation notes.

**Acceptance Scenarios:**

1. **Given** each of the four clients, **When** the operator runs the connect
   flow, **Then** OAuth completes and at least one tool call succeeds — or the
   blocker is recorded and escalated (`block`) for an operator decision.

## Functional Requirements

- **FR-001**: `/mcp` MUST require OAuth on every request; unauthenticated
  requests MUST return `401` with a `WWW-Authenticate: Bearer` header naming the
  RFC 9728 Protected Resource Metadata URL.
- **FR-002**: The server MUST serve RFC 9728 Protected Resource Metadata and
  Authorization Server metadata (RFC 8414 / OIDC) advertising `/authorize`,
  `/token`, and a registration endpoint, with PKCE support.
- **FR-003**: The server MUST bridge to Google as the upstream identity provider
  (no DCR at Google) and present itself as the authorization server to the MCP
  client, minting a server-issued token bound to this resource's audience.
- **FR-004**: The server MUST validate the presented token's audience and reject
  tokens not minted for `/mcp`.
- **FR-005**: From a verified OAuth identity, the server MUST resolve the
  corresponding `User` (by Google `sub`, consistent with `sync_google_user`),
  creating the user on first sign-in the same way human login does.
- **FR-006**: The server MUST find-or-create exactly **one** canonical per-user
  "Mode A" `Connection` for that user and reuse it on subsequent sessions (idempotent;
  no duplicate connections accumulate per user). Because `connections.user_id` is
  only indexed, **not** unique, application-level find-or-create is race-prone under
  concurrent OAuth callbacks / parallel first tool calls. The plan MUST specify the
  mechanism that guarantees uniqueness under concurrency — e.g. a DB uniqueness
  constraint on a per-user Mode A marker plus a transactional upsert/`SELECT … FOR
  UPDATE`-style lock — not just an in-app check.
- **FR-007**: The Mode A connection MUST satisfy `require_agent_player`'s provider
  join — i.e. it MUST have `connection_providers` enabled such that the user's
  active AI agents (`Agent.kind == AI`, by `Agent.provider`) resolve. The plan
  MUST specify which providers are enabled (e.g. all known providers, or
  on-demand per the user's agents) and keep it correct as agents are added.
- **FR-008**: Authenticated MCP tools MUST act for the resolved user's agents via
  the Mode A connection, producing results identical in shape to today's
  header-authed path. `get_game_state` MUST remain callable without auth (it is
  public today).
- **FR-009**: `require_connection`, the internal HTTP play API, and
  `scripts/agentludum_connector.py` MUST remain functionally unchanged; the raw
  `sk_conn_` path MUST continue to authenticate.
- **FR-010**: No `sk_conn_` secret and no OAuth token may appear in a URL or in
  the chat prompt at any point in the connect or play flow.
- **FR-011**: The Mode A connection MUST honor the existing lifecycle gates —
  deleted (`410`), paused (`403`), and disabled-account (`403`) — consistent with
  `require_connection`, so an operator can pause/stop an OAuth player.
- **FR-012**: The credential the `/mcp` layer uses to call the internal API on
  behalf of the Mode A connection MUST NOT be a plaintext long-lived secret stored
  at rest. The system stores only key *hashes* today (`key_lookup`, `key_hint`);
  the plan MUST choose a mechanism that preserves this posture (candidates in
  "Open design points").
- **FR-013**: Configuration (Google client id/secret, server base URL, JWT
  signing key, redirect URIs) MUST be supplied via settings/env, fail loud in a
  real deployment when missing (mirroring `_check_oauth_config`), and warn-but-run
  in local dev. No secret committed.
- **FR-014**: `pyproject.toml` MUST pin the chosen `fastmcp` v3 version; `mypy`
  over `app/` and `mcp_server/` and `ruff` MUST pass with no suppressions; `pytest`
  MUST pass with new tests for the bridge logic (mocking Google and the token
  verifier — never the DB in integration tests).

## Key entities

- **Per-user "Mode A" Connection** — one canonical `Connection` per `User`,
  representing that user's OAuth/MCP play session. A real connection (pause/resume,
  concurrency, stall, dashboard all apply), distinguished from connector-created
  connections by a stable marker (e.g. nickname/provider/setup-source — plan
  decides). Reused across sessions; never duplicated per user.
- **OAuth identity → User** — resolved by Google `sub` exactly as `sync_google_user`
  does, so the MCP user and the human-login user are the same row.

## Edge cases

- **User with zero agents** completes OAuth → tool calls return `waiting` /
  "no agent in game"; agent creation stays on the web (non-goal to do it via MCP).
- **User owns agents on multiple providers** → the Mode A connection must resolve
  all of them (FR-007); adding a new agent on a not-yet-enabled provider must not
  silently break that agent's turns.
- **Concurrent sessions / parallel tool calls** for the same user must not corrupt
  the Mode A connection's credential or create duplicate connections (FR-006,
  FR-012).
- **Paused / deleted / disabled** Mode A connection or account → same error
  semantics as the header path (FR-011).
- **Token expiry / refresh mid-game** → client re-auth must resume cleanly without
  losing the user's place in a match.
- **A client that cannot complete spec OAuth** (DCR/PKCE) → no `/mcp` fallback;
  documented connector path + `block` escalation (Risks).
- **`get_game_state`** (public, no auth) must keep working under the OAuth-gated
  server — confirm the provider does not blanket-gate this tool.

## Success criteria

- **SC-001**: A player connects each of the four clients via Google OAuth with no
  pasted key / no key in URL / no key in chat, and completes a turn end-to-end.
- **SC-002**: `curl -i /mcp` with no token returns spec-compliant `401` +
  `WWW-Authenticate`; the discovery docs fetch and the PKCE/DCR flow completes.
- **SC-003**: Connector and direct `sk_conn_` HTTP API show zero regressions.
- **SC-004**: No duplicate Mode A connections accumulate for a user across repeated
  connects.
- **SC-005**: Preflight green (ruff + mypy `app/ mcp_server/` + pytest), no
  suppressions.

## Open design points (for the plan/design stage — not reopening discovery)

1. **Credential mechanism for the internal loopback (load-bearing, FR-012).** The
   system never stores raw keys. Candidates: (a) generate/store the Mode A raw key
   **encrypted at rest** (new column, symmetric encryption with an app secret),
   decrypt to forward; (b) mint a **short-lived internal token** per session/request
   bound to the connection; (c) bring forward the **in-process service call** so the
   `/mcp` tools resolve the `Connection` directly and skip the HTTP loopback (the
   discovery non-goal defers this, but the plan may revisit if it is the cleanest
   safe option). The plan must pick one and state its verification.
2. **Provider enablement for the Mode A connection (FR-007).** All known providers
   enabled vs on-demand per the user's agents; must stay correct as agents are added.
3. **FastMCP migration surface.** Identify the exact import/transport/auth-config
   renames moving from `mcp.server.fastmcp` to standalone `fastmcp`, and confirm the
   `streamable_http_app()` mount + the existing lifespan wiring in `app/main.py`
   still drive the session manager (today the parent app explicitly runs the MCP
   sub-app's lifespan — see `app/main.py:158-172`).
4. **Mounting the OAuth/metadata routes.** Where the `/.well-known/*` and
   `/authorize`/`/token`/`/register` endpoints live relative to the `/mcp` mount and
   Railway's TLS proxy (the server already disables DNS-rebinding host checks behind
   the proxy — see `mcp_server/server.py:32-36`); confirm the public base URL the
   metadata advertises is correct behind the proxy.
5. **JWT signing key** lifecycle and storage (FR-013).
6. **Per-client connect snippets** for `docs/setup-mcp.md` (and noting the existing
   doc drift: it still shows `X-Agent-Key`/`sk_bot_` while live code uses
   `X-Connection-Key`/`sk_conn_`).
7. **Public tool under the OAuth gate (FR-008).** `get_game_state` is unauthenticated
   today, but a `GoogleProvider`/`OAuthProxy` gates the *whole* `/mcp` app — so the
   gate would accidentally hide the public tool. The plan MUST decide how
   `get_game_state` stays reachable without a token (a public carve-out / separate
   unprotected mount) **or** consciously make it auth-required, and MUST add a test
   that asserts the chosen behavior so it can't silently regress.

## Non-goals

- Changing the connector's auth (keeps raw `sk_conn_`).
- Removing the `sk_conn_` / `require_connection` auth path on the internal HTTP API.
- Refactoring `require_connection` to accept OAuth identity directly (Option B,
  rejected).
- Cursor support (explicitly dropped).
- MCP-driven new-user onboarding / agent creation via the OAuth flow.
- The in-process loopback refactor as a *required* deliverable (may be revisited
  only as a candidate for the FR-012 credential mechanism).

## Risks

- **R1 — Client MCP-OAuth maturity (HIGH).** All four clients gate shipping, but
  Codex/Gemini CLI spec-OAuth support is outside our control and there is no
  `/mcp` header fallback. *Verification:* run the real connect flow on each client
  early in the plan stage; if a client cannot do spec OAuth, escalate via `block`
  for an operator decision (ship-without / wait / per-client documented connector
  path) rather than working around it in code.
- **R2 — Spec-compliance mismatch (HIGH).** A wrong discovery handshake or audience
  rule blocks all clients and is painful to fix post-merge. *Verification:* assert
  the 401/`WWW-Authenticate`/PRM/AS-metadata chain with `curl` and an automated test
  before any client validation.
- **R3 — Hot-path regression (HIGH).** Even though `require_connection` is untouched,
  the Mode A connection flows through it. *Verification:* connector + direct-API
  regression tests stay green; new tests cover paused/deleted/disabled Mode A
  connection semantics.
- **R4 — Credential-at-rest posture (MED).** FR-012 must not introduce a plaintext
  long-lived secret. *Verification:* review the chosen mechanism; grep for any raw
  key persisted in plaintext.
- **R5 — FastMCP migration breakage (MED).** Standalone `fastmcp` renames could
  break the mount/lifespan or the 9 tools. *Verification:* `/mcp` smoke test (tools
  list + one call) after migration, before adding OAuth.

## Assumptions

- We reuse the existing Google OAuth app/credentials (client id/secret) used for
  human login; the MCP OAuth proxy is an additional consumer of the same Google
  app, with its own redirect URIs registered.
- Each client's current MCP-OAuth support is verified early in the plan stage; a
  client that cannot do spec OAuth is escalated via `block`, not worked around.
