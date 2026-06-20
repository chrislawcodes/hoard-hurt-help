# Reuse audit — mcp-prompt-tools-cleanup

## Key findings

**1. Exact builders `get_instructions` should reuse for each of its four sections**

- **"## The rules"** — call `module.rules_text(total_rounds, turns_per_round)` on the
  `GameModule` contract (`app/games/base.py:110`), which for HHH delegates to
  `make_game_rules_text` (`app/games/hoard_hurt_help/rules.py:60`). Do NOT call
  `make_rules_text` (`rules.py:74`) — that appends `RESPONSE_PROTOCOL` (the
  "return one JSON object" contract) which is connector-only and must not appear
  on the MCP path.
- **"## You"** — `your_agent_id` and the target list are already derived in
  `_build_turn_payload` at `agent_play_next_turn.py:279–280`. The pattern
  `seat_name_by_agent_id[player.agent_id]` / `sorted(seat_name_by_agent_id.values())`
  can be lifted verbatim, or `get_instructions` can run the same two queries
  (all Players for the match) and replicate the logic. No dedicated helper exists,
  but the four lines are trivial to copy without risk of drift.
- **"## Your strategy"** — `version.strategy_text` from the agent's
  `AgentVersion` row (`app/models/agent_version.py`). The version is already
  loaded in `_collect_candidates` at `agent_play_next_turn.py:127` and stored in
  `ctx["version_by_agent_id"]`. A new tool resolving via `_resolve_oauth_connection`
  has the `Connection`; it can run the same `agents_stmt` join or a simpler
  single-agent variant to get the `AgentVersion`.
- **"## How to answer"** — pure new text (MCP-specific "call the tools" wording).
  `RESPONSE_PROTOCOL` (`app/agent_prompt.py:17`) must NOT be reused here; it is
  the connector contract. A new string literal lives only in `mcp_server/server.py`.

**2. Cleanest seam for the lean-payload isolation**

The two options are (a) a `channel` / `audience` param on `agent_play_next_turn.get_next_turn`
that strips static fields before returning, or (b) post-processing in the MCP
`get_next_turn` wrapper in `mcp_server/server.py`.

**Option (b) — strip in the MCP wrapper — is far easier and safer.**

Evidence:
- `get_next_turn` in `agent_play_next_turn.py:370` returns a plain `dict[str, object]`.
  The MCP wrapper at `mcp_server/server.py:521–540` simply calls `play_get_next_turn`
  and `return`s the result directly. There is already no output transformation there.
- Stripping `static.base_prompt`, `static.rules`, `static.your_strategy`, and
  the top-level `strategy` key from the returned dict is three lines of Python
  in the wrapper — no new parameter, no new code path, no risk of breaking the
  connector's call at `agent_next_turn.py:28` which calls the same function with
  identical args.
- Adding a `channel` param to `get_next_turn` would require threading it through
  `_serve_one_turn` (line 330) → `_build_turn_payload` (line 251) and would touch
  the shared service layer, which the architecture explicitly warns to keep free of
  adapter-specific concerns (arch doc §Notable shapes: "keep new play behavior in
  the service layer, not in one adapter").
- The same post-process approach works for `get_next_turns` (the multi-agent
  fan-out): its wrapper at `server.py:543–557` also returns the raw dict and can
  strip the same keys from each turn in the `turns` list.

**3. Duplication risks**

- `make_rules_text` vs `make_game_rules_text` — the two functions in
  `rules.py:74` and `rules.py:60` are easy to confuse. `get_instructions` must use
  `make_game_rules_text` (rules only) not `make_rules_text` (rules + connector
  response protocol). Add a comment when calling it.
- Agent identity derivation (`your_agent_id` / target list) exists only inside
  `_build_turn_payload` (private). If `get_instructions` re-derives these from
  scratch, the two copies could drift. The risk is low (the logic is four lines),
  but it is worth a shared helper or at least a comment cross-referencing
  `agent_play_next_turn.py:279–280`.
- `CHAT_INSTRUCTIONS` in `app/agent_prompt.py:27` is used in `make_agent_base_prompt`.
  The spec's `get_instructions` content does not include it (the four sections
  are rules/you/strategy/how-to-answer). Make sure it is not silently dropped
  from the connector path — it is only present via `make_agent_base_prompt`, which
  stays in the connector payload; the MCP path does not need it separately.

---

## Capability table

| Capability | Existing module (path:line) | Verdict | Note |
|---|---|---|---|
| Game rules text (semantic rules only, no response format) | `app/games/hoard_hurt_help/rules.py:60` `make_game_rules_text` | **reuse** | Called via `GameModule.rules_text` but that wraps `make_rules_text` which appends `RESPONSE_PROTOCOL`; call `make_game_rules_text` directly or via the module after adding a rules-only hook |
| Game rules text + connector response protocol | `app/games/hoard_hurt_help/rules.py:74` `make_rules_text` | **connector-only** | Used by `module.rules_text()`; deliberately NOT reused in `get_instructions` |
| Agent base prompt (connector) | `app/agent_prompt.py:34` `make_agent_base_prompt` | **connector-only** | Embeds `RESPONSE_PROTOCOL`; must not appear on MCP path |
| `RESPONSE_PROTOCOL` constant | `app/agent_prompt.py:17` | **connector-only** | "Return exactly one JSON object"; must not appear in MCP `get_instructions` or MCP turn payload |
| `CHAT_INSTRUCTIONS` constant | `app/agent_prompt.py:27` | **reuse if desired** | Available for reference; not required in MCP `get_instructions` four-section layout |
| `your_agent_id` derivation | `app/engine/agent_play_next_turn.py:279` | **extend** | Private to `_build_turn_payload`; `get_instructions` must replicate (4 lines) or a thin helper extracted |
| `all_agent_ids` / target list derivation | `app/engine/agent_play_next_turn.py:280` | **extend** | Same — private; copy pattern from `_build_turn_payload` |
| Agent `strategy_text` retrieval | `app/engine/agent_play_next_turn.py:127,297` `AgentVersion.strategy_text` | **reuse** | Same query pattern `_collect_candidates` already uses; or a simpler single-agent query in the tool |
| Per-turn static payload builder (`static` block) | `app/engine/agent_play_next_turn.py:281–298` `_build_turn_payload` | **extend** | MCP wrapper strips `static.base_prompt`, `static.rules`, `static.your_strategy` post-hoc; no service-layer change needed |
| MCP `get_next_turn` wrapper | `mcp_server/server.py:521–540` | **extend** | Add 3-line strip of static keys here; connector route `agent_next_turn.py:28` is untouched |
| MCP `get_next_turns` wrapper | `mcp_server/server.py:543–557` | **extend** | Strip same keys from each item in `turns` list |
| OAuth → Connection resolution | `mcp_server/server.py:409–423` `_resolve_oauth_connection` | **reuse** | `get_instructions` uses this exactly as other authenticated tools do |
| OAuth → Player resolution | `mcp_server/server.py:478–494` `_resolve_oauth_player` | **reuse if match_id known** | `get_instructions` needs agent+version but not necessarily a Player; `_resolve_oauth_connection` + a direct agent query may be cleaner |
| "How to answer" MCP-specific wording | — | **justified-new** | No existing string; must say "call submit_talk/submit_action", never "return JSON" |
| Kickoff prompt (`_PLAY_PROMPT`) | `app/routes/connections_connect_guide.py:181` | **extend** | Rewrite in place; remove references to cut tools; add `get_instructions` call |
| MCP DB session dependency | `mcp_server/server.py:257–268` `_session_scope` | **reuse** | All authenticated tools use `Depends(_session_scope)`; `get_instructions` does the same |
| `GameModule` contract hook `rules_text` | `app/games/base.py:110` | **reuse** | Platform-agnostic; but see note above — delegates to `make_rules_text` which appends connector protocol |
| `GameModule` contract hook `agent_base_prompt` | `app/games/base.py:120–128` | **connector-only** | Produces the full connector prompt; `get_instructions` must not call this |
| Connector route → service wiring | `app/routes/agent_next_turn.py:28` → `agent_play_next_turn.get_next_turn` | **leave unchanged** | This is the invariant the spec protects; touch nothing here |
