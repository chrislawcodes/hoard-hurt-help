# Spec: MCP play prompt + tools cleanup

**Slug:** mcp-prompt-tools-cleanup
**Branch:** feat/mcp-prompt-tools-cleanup

## Background

There are two ways an AI plays our games:

- **Machine connection (connector):** a background daemon runs CLI agents and
  drives the loop itself. It reaches the per-turn payload over HTTP at
  `/agent/next-turn` (`app/routes/agent_next_turn.py`), which calls
  `app.engine.agent_play.get_next_turn`. **This path works well and must not
  change.**
- **MCP:** the user pastes a kickoff prompt into their AI client (Claude Code,
  Codex, Gemini CLI, Claude Desktop); the AI calls MCP tools in a loop. It
  reaches the same per-turn payload through the `get_next_turn` tool in
  `mcp_server/server.py`.

Both paths call the **same** payload builder
(`app/engine/agent_play_next_turn.py`, the `static`/`payload` block around
lines 281–319). That builder ships, on **every turn**:

- `static.rules` — the full game rules (`make_rules_text`), which already
  contains the JSON `RESPONSE_PROTOCOL`.
- `static.base_prompt` — `make_agent_base_prompt` (`app/agent_prompt.py`), which
  embeds the **same rules again** plus chat tips plus the **same
  `RESPONSE_PROTOCOL` again**.
- the agent strategy **twice** — top-level `strategy` and `static.your_strategy`.

So on the MCP path each turn re-sends ~1,100 words of unchanging instructions,
roughly half of it literal duplication. Worse, `RESPONSE_PROTOCOL` tells the AI
to "Return exactly one JSON object with no prose or code fence" — correct for
the **connector** (which replies with raw JSON) but **wrong for MCP** (which
must call the `submit_talk` / `submit_action` tools). And the MCP tool surface
has grown to 10 tools, several of which only re-serve data the turn payload
already carries.

This feature restructures the **MCP path only** into three clean layers and
trims its tool set. The connector path is left byte-for-byte unchanged.

## Design decisions already made (discovery)

1. **Three layers for MCP:** *Kickoff* (the paste-once prompt — loop control),
   *Instructions* (static "how to play", fetched once via a tool), *Turn* (only
   what changed, every turn).
2. **Tool name is `get_instructions`** — most literal/triggering name for an
   LLM. ("Briefing" is internal shorthand only.) Avoid `get_game_info` (clashes
   with the existing `get_game_state`).
3. **One `get_instructions` call, four labeled sections** (not multiple tools):
   `## The rules`, `## You`, `## Your strategy`, `## How to answer`.
4. **Scope = full restructure** (not just de-dup).
5. **Clean break** on removed tools — no deprecation shims (alpha; a mid-game
   player re-pastes the updated prompt).
6. **Keep `get_game_state`** — verified it is not a play-loop-only tool: it is a
   unique "inspect any public game" capability and part of the leak-test
   surface. Cutting it would touch security tests for no real simplification.
7. **Connector untouched** — the shared builder must keep emitting today's exact
   payload for the connector.

## Changes (MCP path only)

### 1. New `get_instructions` MCP tool — `mcp_server/server.py`

Add a tool that returns ONE text response, the static "how to play" pack, split
into four labeled sections.

**Game-agnostic (reconciles Codex plan MEDIUM #1).** The platform runs multiple
games (Hoard-Hurt-Help and Liar's Dice today), and the MCP loop fans out over
all of a user's active agents across games. `get_instructions` MUST resolve the
selected agent's match → its `GameModule` and get the game-specific rules and
"how to answer" from the module — it must NOT hardcode HHH. Add a new
game-agnostic `GameModule` method (e.g. `mcp_play_instructions(*, your_agent_id,
all_agent_ids, total_rounds, turns_per_round) -> str`) that returns the
game-specific sections WITHOUT the connector's JSON `RESPONSE_PROTOCOL`:
implement it for HHH (reusing `make_game_rules_text`; actions HOARD/HELP/HURT via
`submit_action`) and Liar's Dice (its own rules + private-dice note + bid/
challenge answer format), with a base-class default. `get_instructions` calls the
module method and inserts the per-agent `## Your strategy` section.

The HHH-flavored example below illustrates the shape; Liar's Dice returns its own
rules and answer format from the same seam:

```
How to play Hoard-Hurt-Help

## The rules            (same for every player)
<make_game_rules_text(total_rounds, turns_per_round)>

## You
You are "<your_agent_id>". You can target: [<other agent ids>].

## Your strategy        (your owner's plan for how you play)
<agent version strategy_text>

## How to answer
Talk phase  -> call submit_talk(message, thinking, ...).
Act phase   -> call submit_action(action, target_id, thinking, ...).
Call the tool. Do not reply with JSON or plain text.
```

**Selection rule (multi-agent — reconciles Codex spec HIGH).** `get_instructions`
takes optional `agent_id` and `match_id` selectors, mirroring `get_next_turn`:
- `agent_id` given → return that agent's identity, targets, and strategy (for its
  active match; `match_id` disambiguates if that agent is in more than one).
- no selector + exactly one active agent → use it.
- no selector + multiple active agents → return the rules + "How to answer" plus a
  "You have multiple agents — call get_instructions(agent_id=…) for each one's
  strategy" note listing the agent_ids. (Rules + how-to-answer are identical for
  all agents; only "You" and "Your strategy" are agent-specific.)
- no active game/agent → return rules + how-to-answer with a note that there's no
  active game yet.

This matches the documented parallel-play flow: each per-agent loop calls
`get_instructions(agent_id=…)` once for its own strategy.

### 2. Slim the MCP `get_next_turn` payload — MCP layer

The MCP `get_next_turn` response must contain only live state:
`status`, `match_id`, `turn_token`, `agent_turn_token`, `current`
(phase/deadline), `history`, `scoreboard`, chat, `public_state`. It must **no
longer** contain `static.base_prompt`, `static.rules`, or the duplicated
strategy fields.

**Isolation mechanism (reconciles Gemini spec HIGH #1).** Do NOT modify the
shared `_build_turn_payload` / `agent_play.get_next_turn` builder or the connector
HTTP route. Instead, reshape in the **MCP wrappers**: `mcp_server/server.py`
`get_next_turn` (and `get_next_turns`) already return the dict from the shared
service; after calling it, produce the lean shape before returning. The shared
builder and the connector route are byte-for-byte untouched.

**Drop only the heavy duplicates — preserve all live state (reconciles Codex
spec re-review HIGH/MEDIUM + Gemini HIGH #2).** The strip must remove ONLY the
three bloat/duplicate fields and keep everything else, because several top-level
and `static` fields are LIVE per-turn state, not duplicate rules:
- top-level `your_private_state` (e.g. Liar's Dice hidden dice — dropping it makes
  hidden-info games unplayable),
- top-level `public_state` (board state),
- `static.coach_note` (per-round coaching guidance, injected only on coached
  turns).

So: drop top-level `strategy`, and from `static` drop ONLY `rules`,
`base_prompt`, `your_strategy`. Keep every other top-level key (incl.
`your_private_state`, `public_state`, `history`, `scoreboard`, `current`,
`tokens`) and every other `static` key (incl. `coach_note`, `total_rounds`,
`turns_per_round`, `your_agent_id`, `all_agent_ids`). Use `dict.pop(key, None)`
(no `KeyError`). A test pins the exact MCP `static` key set AND asserts
`your_private_state`/`coach_note` survive when present — so a future static key is
caught by the test (the tripwire against silent re-bloat) and live state can never
be dropped. The static rules/strategy/format now come from `get_instructions`.

**Apply to BOTH MCP turn tools (reconciles Gemini spec MEDIUM #3).** Both
`get_next_turn` and `get_next_turns` go through the same shared builder, so the
lean strip must be applied in **both** MCP wrappers, not just `get_next_turn`.

**Full history/scoreboard are retained (reconciles Codex spec MEDIUM).** The lean
payload keeps the complete `history` (the full public move log —
`_load_public_action_records` loads every public action for the match, not a
window) and `scoreboard`. So the data that `get_standings`, `get_turn_detail`, and
`get_opponent_history` re-serve is still present every turn; the AI filters the
full `history` itself. The only thing NOT in the turn payload is older **chat**
beyond the current turn — which is why `get_chat` is kept.

### 3. Response-format guidance split — `app/agent_prompt.py` / MCP

`RESPONSE_PROTOCOL` (the "return one JSON object" contract) stays in use for the
**connector** path. Nothing the **MCP** path emits may instruct the AI to return
JSON; MCP's "how to answer" (in `get_instructions`) says to call the tools.

### 4. Cut 4 redundant MCP tools — `mcp_server/server.py`

Remove (clean break): `get_turn` (duplicate of `get_next_turn`), `get_standings`
(scoreboard is in every turn), `get_turn_detail` and `get_opponent_history` (both
re-serve a subset of the full `history` the lean turn payload still carries — see
§2). No capability is dropped: the full move log and scoreboard remain in every
`get_next_turn`, and `get_chat` covers older chat.
**Keep:** `get_next_turn`, `submit_talk`, `submit_action`, `get_chat` (the one
"catch up if your context was trimmed" tool), `get_next_turns` (multi-agent
fan-out), `get_game_state`. **Add:** `get_instructions`. Net 10 → 7. Update
`mcp_server/README.md` and the tool-list assertions in `tests/test_mcp.py`.

### 5. Rewrite the Kickoff prompt — `_PLAY_PROMPT`, `app/routes/connections_connect_guide.py`

MCP-only paste prompt. Lead with one hard rule (keep calling `get_next_turn`
yourself; never pause to ask; never hand control back while waiting; stop only
on a turn to play or `should_stop=true`). Tell it to call `get_instructions`
first (and to re-call it if it loses the rules). Handle the two waiting states
distinctly: `waiting` → in a game, call again right away (each call waits ~25s;
don't sleep); `no_game` → check `should_stop` (true → stop cleanly and tell the
user; false → a game is scheduled soon, wait `next_poll_after_seconds` then call
again). State the tokens once. Demote the multi-agent fan-out to the end. Remove
references to the now-cut tools (`get_opponent_history`, `get_standings`); point
catch-up only at `get_chat`. **Keep the opening sentence exactly** "You are
playing Hoard Hurt Help through the agentludum MCP tools." (a test pins this
substring).

## Out of scope / non-goals

- Any connector/CLI path behavior change.
- MCP Tasks extension (no target client supports it yet — tracked separately).
- Fixing Gemini/Codex client-side loop bugs (upstream).
- Deprecation shims for removed tools.

## Acceptance criteria

- **AC-1:** `get_instructions` returns one response with the four labeled
  sections; "How to answer" instructs calling `submit_talk`/`submit_action`,
  never "return JSON"; the strategy section carries the agent's strategy text.
- **AC-2:** the MCP `get_next_turn` payload contains no `base_prompt` and no
  duplicated rules/strategy; a regression test asserts the connector
  `/agent/next-turn` payload still contains `base_prompt` and `rules`.
- **AC-3:** the MCP tool list equals exactly: `get_instructions`,
  `get_next_turn`, `get_next_turns`, `submit_talk`, `submit_action`, `get_chat`,
  `get_game_state`.
- **AC-4:** the kickoff prompt keeps the pinned opening sentence and no longer
  names the removed tools.
- **AC-5:** Preflight gate passes — `ruff`, `mypy`, `pytest`.

## Verification

- `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q` all green.
- New tests: `get_instructions` section/content + tool-call format; MCP
  `get_next_turn` omits `base_prompt`/`rules`; connector `/agent/next-turn`
  still includes them; MCP tool list = the 7 names.
- Connector regression: an existing connector next-turn test still passes
  unchanged.
