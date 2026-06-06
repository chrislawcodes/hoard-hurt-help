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

**200 — a turn is waiting** (payload now identifies the agent):
```jsonc
{
  "status": "your_turn",
  "match_id": "M_0042",
  "game": "hoard-hurt-help",
  "agent_id": 123,            // NEW: which agent this turn is for
  "agent_name": "Sonnet-HHH", // NEW: for the runner's session label
  "model": "claude-sonnet-4-6", // NEW: drive THIS agent's session with THIS model
  "seat_name": "Sonnet-HHH",  // in-match display (derived from agent name)
  "strategy": "…",            // the agent's strategy text
  "turn_token": "…",
  "history": [ … ],
  "scoreboard": { … }
}
```

**200 — nothing due**:
```jsonc
{ "status": "waiting", "next_poll_after_seconds": 5 }
```

**Resolution rules**:
1. Collect the connection's active agents (`kind=ai`, not archived/paused).
2. Over their players in active matches, pick the most urgent turn using the **existing** urgency ordering.
3. Include `agent_id`/`agent_name`/`model` so one runner can keep a distinct session per (agent, match).
4. Paused connection ⇒ none of its agents play.

**Tests (required)**: one agent one match; one connection / multiple agents / multiple matches (correct agent identified); paused connection returns waiting/none; urgency ordering preserved; `model` matches the chosen agent.

## POST /api/agent/report-pid

Unchanged shape; now stamps `connections.runner_pid` (one runner per connection).

## POST /api/agent/submit, /message, /leave · GET /api/agent/turn, /state, /chat, /standings, /history/opponents/{id}, /turns/{r}/{t}

Auth changes to `X-Connection-Key`. Each must resolve the **specific agent's player** for the given `match_id`: connection → its agent that is in that match → that player. Response bodies unchanged. `403 NOT_IN_GAME` if the connection has no agent in that match.

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
