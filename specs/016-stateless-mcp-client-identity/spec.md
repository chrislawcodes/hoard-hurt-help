# Spec 016 — Stateless MCP Client Identity

**Created**: 2026-06-17  
**Status**: Draft  
**Branch**: TBD

---

## Summary

The MCP server runs in `stateless_http=True` mode so Railway redeploys don't orphan connected clients. But the code that figures out *which* AI client (Gemini, Claude Code, etc.) is calling still reads from session memory — which is wiped between requests in stateless mode. The result: any user with two or more active MCP connections gets HTTP 400 `UNKNOWN_MCP_CLIENT` on every tool call. This fix moves identity resolution to the OAuth token, which is stable and present on every request.

---

## The Bug

### What should happen

A user connects both Gemini CLI and Claude Code. Both clients make tool calls (e.g. `get_next_turns`). Each call resolves to that client's own connection, which has the right `last_polled_at` heartbeat updated.

### What actually happens

Every tool call fails with:

```json
{ "error": { "code": "UNKNOWN_MCP_CLIENT", "message": "Couldn't tell which AI client connected..." } }
```

### Why

`_resolve_oauth_connection` calls `_client_provider_from_context()` to figure out the provider. That function reads `ctx.session.client_params.clientInfo.name` — the client's self-reported name from its `initialize` handshake. In stateless mode, each HTTP request is a fresh session with no memory of prior requests. `client_params` is `None` on tool calls. Provider resolves to `None`. `mcp_connection_for(provider=None)` can only return a connection when the user has exactly one — with two connections it returns `None`, and the tool raises `UNKNOWN_MCP_CLIENT`.

**Single-connection users are lucky, not correct.** The fallback works by accident. As soon as they connect a second provider the bug surfaces.

### Proof

Two tests in `tests/test_mcp.py` prove this (added in the previous session):

- `test_client_provider_from_context_is_none_outside_fastmcp_session` — shows the mechanism: `_client_provider_from_context()` returns `None` outside a live FastMCP request.
- `test_multi_connection_user_gets_unknown_mcp_client_when_provider_is_none` — shows the impact: provider `None` + 2 connections = HTTP 400 `UNKNOWN_MCP_CLIENT`.

---

## Fix

### Core idea

The OAuth access token has a `client_id` field — the registered OAuth client's ID. It is:

- Stable: the same client always presents the same `client_id`
- Present on every request: the token is verified on every authenticated call
- Already available: `token.client_id` is readable today in every tool handler via `_require_access_token(token).client_id`

We store `oauth_client_id` on the `Connection` row at `initialize` time (when we know both the token's `client_id` and the `clientInfo.name`). On subsequent tool calls we pass `token.client_id` to the connection lookup and find the right connection directly, bypassing the broken session-memory path.

### Data model change

Add one nullable column to `connection`:

```
oauth_client_id  VARCHAR(255)  NULLABLE
```

No backfill. Existing connections start with `NULL`. When a client reconnects, the `initialize` handshake writes the `oauth_client_id`. Until then, the single-connection fallback keeps working as before.

### Code changes

| File | Change |
|------|--------|
| `app/models/connection.py` | Add `oauth_client_id: str \| None` field |
| `migrations/versions/XXX.py` | Add nullable `oauth_client_id` column to `connection` |
| `app/engine/mcp_connection.py` | Accept `oauth_client_id` in `mcp_connection_for`; look up by that column first when provided |
| `mcp_server/server.py` | Pass `token.client_id` through `_connection_from_token` → `mcp_connection_for`; write it on initialize; remove `_client_provider_from_context()` from the tool-call path |

### Lookup logic after the fix

```
_resolve_oauth_connection(db, token):
    oauth_client_id = token.client_id        # always present
    provider = _client_provider_from_context()  # still attempted, advisory
    connection = await mcp_connection_for(
        db, user,
        provider=provider,
        oauth_client_id=oauth_client_id,     # new: primary key for lookup
    )
```

Inside `mcp_connection_for`:
1. If `oauth_client_id` is given, look for a live connection row where `oauth_client_id = ?` AND `user_id = ?`. Return it if found.
2. Otherwise fall back to the existing `provider` lookup (same as today).
3. If neither matches, fall back to the single-existing-connection path.
4. Return `None` only when none of the above finds anything (genuinely new client + unknown provider).

On `initialize` (in `SigninConnectionMiddleware.on_initialize`):
- We already have `provider` (from `clientInfo.name`) and `token.client_id`.
- Write both to the connection row. `oauth_client_id` is the stable key; `provider` keeps driving the connections page display.

### What changes for users

- **Multi-connection users** (Gemini + Claude Code): tool calls stop returning 400. Both clients resolve their own connection.
- **Single-connection users**: no change; they already worked and still do.
- **New clients** (first `initialize`): same as today — connection is created with both `provider` and `oauth_client_id` set.
- **Clients that connected before this deploy** (no `oauth_client_id` yet): their `initialize` handshake writes it on first reconnect. Until then the `provider` path stays as the fallback (works for single-connection users).

---

## Out of scope

- Changing how `clientInfo.name` is used on the connections page (display still keys off `provider`)
- Multi-agent parallelism (`agent_id` path in `get_next_turn`) — separate concern
- Any change to the OAuth registration flow or the KV store

---

## Requirements

**FR-001** A user with two active MCP connections (e.g. Gemini + Claude Code) MUST be able to call any MCP tool without receiving `UNKNOWN_MCP_CLIENT`.

**FR-002** Each client's tool call MUST resolve to that client's own connection — not a different provider's connection — so heartbeats and usage counts are attributed correctly.

**FR-003** The fix MUST NOT break single-connection users or clients that connected before this deploy.

**FR-004** The `oauth_client_id` column MUST be written on the `initialize` handshake and read on subsequent tool calls. It MUST NOT require a backfill of existing rows.

**FR-005** The migration MUST be additive (nullable column, no backfill required) and verified by the standard row-count post-migration check.

---

## Acceptance tests

1. **Two-provider user, tool call resolves correctly**: create a user with two connections (Gemini provider + Claude provider, each with a distinct `oauth_client_id`). Call `get_next_turns` with each client's token. Each call returns a successful response and stamps the right connection's `last_polled_at`.

2. **`UNKNOWN_MCP_CLIENT` is gone**: the proof test `test_multi_connection_user_gets_unknown_mcp_client_when_provider_is_none` must be inverted — it currently passes because it proves the bug exists. After the fix, the same scenario (provider=None, 2 connections) resolves via `oauth_client_id` instead of failing.

3. **Single-connection fallback still works**: a user with one connection and no `oauth_client_id` set (simulates a pre-deploy client) still gets a valid connection on tool calls.

4. **`initialize` writes `oauth_client_id`**: after an `initialize` handshake, the connection row has `oauth_client_id` set to the token's `client_id`.

---

## Open questions

1. **Is `token.client_id` unique per AI product, or per OAuth registration?** If two Gemini CLI instances belonging to different users share a `client_id` (because they registered the same OAuth app), our lookup needs `(user_id, oauth_client_id)` — which is what the spec proposes. Confirm: does FastMCP issue a *per-user-registration* `client_id`, or is it global per client app?

2. **Should we remove `_client_provider_from_context()` entirely, or keep it as a secondary advisory?** Keeping it means the `provider` column continues to be set accurately on initialize (no change there). Removing it from the tool-call path is the fix; whether to delete the function or leave it as dead code is a judgment call.

3. **What happens when an existing client re-registers (e.g. `/mcp auth agentludum` again)?** They get a new `client_id`. The old connection row's `oauth_client_id` becomes stale. The `initialize` handshake must update it. Does `mcp_connection_for` need to handle "update `oauth_client_id` on an existing connection found by `provider`"?
