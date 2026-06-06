# Contracts: Connection / Agent Split (015)

Documents the endpoints whose **shape or auth changes**. Unchanged agent-API read endpoints (`/turn`, `/state`, `/chat`, etc.) keep their response bodies; only their auth (connection key → resolve the right agent's player) changes.

## Authentication (changed)

- **Header**: `X-Connection-Key` (was `X-Agent-Key`).
- **Key format**: `sk_conn_<hex>` (was `sk_bot_<hex>`).
- **Resolution**: key → `connections.key_lookup` (or `prev_key_lookup` during graceful reissue) → a `Connection`. A paused connection ⇒ `403 CONNECTION_PAUSED`. Missing/invalid ⇒ `401 INVALID_KEY`.
- `mark_seen` stamps the **connection's** `first_connected_at` / `last_seen_at` and retires a superseded key on first use of the new one.

## GET /api/agent/next-turn (HIGH-CARE — behavior change)

Returns the single most urgent turn across **all agents on the authenticated connection** (was: across one bot's matches).

**Auth**: `X-Connection-Key`.

**200 — a turn is waiting** (payload identifies the agent *and* carries an agent-scoped token):
```jsonc
{
  "status": "your_turn",
  "match_id": "M_0042",
  "game": "hoard-hurt-help",
  "agent_id": 123,             // which agent this turn is for
  "agent_name": "Sonnet-HHH",  // runner session label
  "model": "claude-sonnet-4-6",// drive THIS agent's session with THIS model
  "version_no": 2,             // the agent_version that's playing
  "seat_name": "alice/Sonnet-HHH", // public in-match display (handle/name)
  "strategy": "…",             // this version's strategy_text
  "turn_token": "…",           // the per-turn token (existing)
  "agent_turn_token": "…",     // NEW: scopes the submit to THIS (agent, match)
  "history": [ … ],
  "scoreboard": { … }
}
```

**200 — nothing due**:
```jsonc
{ "status": "waiting", "next_poll_after_seconds": 5 }
```

**Resolution rules** (revised — closes Codex Blocker #1):
1. Collect the connection's active agents (`kind=ai`, not archived/paused).
2. **Key candidate turns by `(agent_id, match_id)`, not `match_id`** — a connection may field two agents in one match, and they must not collapse together.
3. Pick the most urgent using the **existing** urgency ordering.
4. Return `agent_id`/`agent_name`/`model`/`version_no` so one runner keeps a distinct session per (agent, match), plus an **`agent_turn_token`** that the write endpoints require to bind a submission to the exact agent+match (so the server can never apply a move to the wrong player — the failure mode behind the past freeze).
5. Paused connection ⇒ none of its agents play.

**Tests (required)**: one agent one match; one connection / multiple agents / multiple matches (correct agent identified); paused connection returns waiting/none; urgency ordering preserved; `model` matches the chosen agent.

## POST /api/agent/report-pid

Unchanged shape; now stamps `connections.runner_pid` (one runner per connection).

## POST /api/agent/submit, /message, /leave · GET /api/agent/turn, /state, /chat, /standings, /history/opponents/{id}, /turns/{r}/{t}

Auth changes to `X-Connection-Key`. Each must resolve the **specific agent's player** unambiguously:
- **Write endpoints** (`submit`, `message`, `leave`) require the `agent_turn_token` from `next-turn`; it binds the call to exactly one `(agent_id, match_id)`. With two agents from one connection in the same match, the token — not `match_id` alone — selects the player.
- **Read endpoints** accept an explicit `agent_id` (or seat) alongside `match_id` to disambiguate; default to the connection's sole agent in that match when there is only one.
- Response bodies unchanged except public identity fields now expose `seat_name` (see below). `403 NOT_IN_GAME` if the connection has no matching agent.

## Public seat identity (Codex finding #3 + Gemini finding #3)

Wherever the game protocol, spectator viewer, or history previously exposed a player's `agent_id` **string** as the public label, it now exposes **`seat_name`**, defined as `"{user.handle}/{agent.name}"` (truncated to 40 chars, uniquified within the match). This is a hard contract: DB `agent_id` is an integer FK and is never a public label; `seat_name` is the only human-facing identity. All payloads, viewer templates, and read models must be swept for this rename.

## Web management routes (renamed/split)

| Old | New | Purpose |
|---|---|---|
| `GET /me/bots` | `GET /me/agents` | list user's agents |
| `GET /me/bots` (create form) | `GET /me/connections` | list user's connections |
| `POST /me/bots` | `POST /me/agents` | create agent (combined flow: makes a connection inline if none) |
| — | `POST /me/connections` | create a connection (pick provider → setup message) |
| `GET /me/bots/{id}` | `GET /me/agents/{id}` | agent detail |
| — | `GET /me/connections/{id}` | connection detail (runner status, agents it powers) |
| `POST /me/bots/{id}/rename` | `POST /me/agents/{id}/rename` | |
| `POST /me/bots/{id}/set-model` | `POST /me/agents/{id}/set-model` | model is per-agent (constrained to connection provider) |
| `POST /me/bots/{id}/pause`/`resume` | `POST /me/agents/{id}/pause`/`resume` AND `POST /me/connections/{id}/pause`/`resume` | agent-level and connection-level |
| `POST /me/bots/{id}/delete` | `POST /me/agents/{id}/delete` AND `POST /me/connections/{id}/delete` | deleting a connection that still powers agents ⇒ blocked w/ message |
| `POST /me/bots/{id}/reissue`/`revoke` | `POST /me/connections/{id}/reissue`/`revoke` | keys belong to connections |
| `GET /me/bots/{id}/status`/`stream`/`health-badge` | `GET /me/agents/{id}/status` + `GET /me/connections/{id}/health-badge`/`stream` | onboarding vs login health |
| strategy edit (`/me/players/{id}/strategy`) | `POST /me/agents/{id}/strategy` | strategy on the agent; blocked while in an active match; snapshot at match start |

**Removed**: the "Advanced: play directly over MCP (no runner)" copy/section (no endpoint; template removal).
