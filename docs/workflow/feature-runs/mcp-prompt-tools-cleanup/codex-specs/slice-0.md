# Implementation Task

## Context
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


## Plan
# Plan: MCP play prompt + tools cleanup

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: HIGH (no-active-game): IMPLEMENTATION CONTRACT — with no active game/agent, get_instructions returns a short 'no active game yet; start one, then call get_instructions again for the game's rules + your strategy' message and OMITS game-specific rules/how-to-answer (no game selected to source them); matches the kickoff no_game handling. MEDIUM (one agent, multiple live matches, match_id omitted): default to the agent's MOST-URGENT match (same ordering as get_next_turn); rules are identical across that agent's matches (same game), only identity/current differ.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: HIGH (stripping re-bloat): addressed — the pinned MCP 'static' key-set test is the tripwire; any new heavy field fails the test and forces a conscious denylist decision. MEDIUM (instruction invalidation): rules are fixed per match; strategy changes only on a new agent version; the kickoff prompt instructs re-calling get_instructions, which covers it. LOW (4 hardcoded sections): accepted — adding a 5th section later is additive; a metadata schema is overkill now.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: MEDIUM#1 (game-agnostic): plan+spec revised — get_instructions now resolves the agent's match's GameModule and calls a NEW game-agnostic module method mcp_play_instructions (base default + HHH + Liar's Dice impls); it never hardcodes HHH. A test asserts a Liar's Dice agent gets LD rules. MEDIUM#2 (selection semantics): explicit selection rule (agent_id/match_id; single/multiple/none); the agent_identity_for helper reuses _collect_candidates' exact active/non-archived/status filters so it can't surface stale/paused agents.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: Identity drift: agent_identity_for reuses _collect_candidates filters + a regression test asserts the connector /agent/next-turn payload is unchanged. Implicit rules contract / RESPONSE_PROTOCOL leak: addressed via the game-agnostic mcp_play_instructions module method (semantic rules only, no protocol) — the structural split suggested. How-to-answer drift: now lives per-game in the module method, staying correct per game (incl. Liar's Dice). Strip-helper leak: depth-based regression test required (plan test plan + residual risks).

## Approach

MCP-path-only restructure. The connector path (HTTP `/agent/next-turn` →
`app.engine.agent_play.get_next_turn` → shared `_build_turn_payload`) and the
JSON `RESPONSE_PROTOCOL` are left untouched. All MCP-specific reshaping happens
in the MCP layer (`mcp_server/server.py`).

## Reuse decisions (from reuse-report.md)

| Capability | Decision | How |
|---|---|---|
| Game-specific RULES for `get_instructions` (not the answer format) | **extend the GameModule interface** | Add `semantic_rules_text(total_rounds, turns_per_round) -> str` to `GameModule` (`app/games/base.py`) — the game's rules WITHOUT the connector's JSON `RESPONSE_PROTOCOL`. Implement for HHH (reuse `make_game_rules_text` `app/games/hoard_hurt_help/rules.py:60`) and Liar's Dice (its `make_game_rules_text`). NEVER use `make_rules_text` / `module.rules_text()` / `module.agent_base_prompt()` (they carry `RESPONSE_PROTOCOL`). `get_instructions` resolves the agent's match's module and uses this for `## The rules` — so it never hardcodes HHH. |
| "How to answer" (MCP) | **justified-new, generic across games** | A single MCP-specific block: talk phase → `submit_talk`; act phase → `submit_action`; call the tool, don't return JSON. It reflects the ACTUAL MCP tools, which today are PD-shaped (`action`/`target_id`). **Out of scope (pre-existing):** MCP `submit_action` does not yet expose the free-form `move` that non-PD games (Liar's Dice) need over HTTP — fully MCP-playable Liar's Dice is a separate follow-up. This feature stops `get_instructions` hardcoding HHH *rules*; it does not claim to make Liar's Dice fully MCP-playable. |
| Caller → connection resolution | **reuse** | `_resolve_oauth_connection` (`mcp_server/server.py`). |
| Agent identity (`your_agent_id`, targets) + `strategy_text` | **extract (from the user's ACTIVE AGENTS, not claimable turns)** | Build `agent_identity_for(db, connection, *, agent_id=None, match_id=None)` returning `(match, your_agent_id, all_agent_ids, strategy_text)`. It must resolve from the user's active (non-archived) agents and their live/scheduled matches — NOT from `_collect_candidates` (which only returns *claimable open turns*, so it would fail the "fetch once at idle / before a turn opens" case). Reuse the agent/version + seat-name derivation (`agent_play_next_turn.py:279–298`) and the active-agent filters, but do not require an open turn. |
| Lean MCP payload | **new (thin)** | Strip static keys in the MCP wrappers — see below. |
| "How to answer" text | **justified-new** | Small MCP-specific string ("call the tools, don't return JSON"). `make_agent_base_prompt` is connector-only and must not be reused here. |

## Implementation slices

### Slice 1 — game-agnostic `get_instructions` tool + identity helper  `[CHECKPOINT]`
- Add `agent_identity_for(db, connection, *, agent_id=None, match_id=None) ->
  (match, your_agent_id, all_agent_ids, strategy_text)` in the engine. Resolve
  from the user's ACTIVE (non-archived) agents and their live/scheduled matches —
  NOT from `_collect_candidates` (claimable open turns only), so it works at idle /
  before a turn opens. Reuse the agent/version + seat-name derivation
  (`agent_play_next_turn.py:279–298`). Do not change `_build_turn_payload`'s
  connector output.
- Add `semantic_rules_text(total_rounds, turns_per_round) -> str` to `GameModule`
  (`app/games/base.py`, base default) — game rules WITHOUT `RESPONSE_PROTOCOL`.
  Implement HHH (`hoard_hurt_help/game.py`, reuse `make_game_rules_text`) and
  Liar's Dice (`liars_dice/game.py`, its `make_game_rules_text`). NEVER use
  `rules_text()` / `make_rules_text` / `agent_base_prompt` (they carry the JSON
  protocol).
- Add the `get_instructions` MCP tool in `mcp_server/server.py`: optional
  `agent_id` / `match_id`; resolve via `_resolve_oauth_connection` +
  `agent_identity_for`; resolve the match's `GameModule`. Assemble four sections:
  `## The rules` = `module.semantic_rules_text(...)`; `## You` (id + targets);
  `## Your strategy` (`strategy_text`); `## How to answer` = a GENERIC MCP block
  (talk→`submit_talk`, act→`submit_action`; call the tool, no JSON). Selection
  rule: `agent_id` given → that agent (use `match_id`, else its most-urgent live
  match — rules are identical across that agent's matches); no selector + one
  active agent → use it; + multiple → return rules + how-to-answer + list
  agent_ids ("call get_instructions(agent_id=…) per agent"); **no active game →
  short "no active game yet; start one, then call get_instructions again" note,
  WITHOUT game-specific rules sections** (no game selected to source them).
  Game-agnostic for rules — never hardcodes HHH. (Full non-PD MCP submit is a
  separate follow-up; see reuse table.)
- Tests (new `tests/test_mcp_instructions.py`): sections present; "How to answer"
  names the tools and contains neither "JSON" nor "RESPONSE FORMAT"; strategy
  section carries the agent's strategy; two active agents →
  `get_instructions(agent_id=X)` returns X's; **a Liar's Dice agent returns
  Liar's-Dice rules, not HHH**; **no active game → the no-game note, no rules
  section**.
- Est: ~200 lines.

### Slice 2 — lean MCP per-turn payload  `[CHECKPOINT]`
- In the MCP `get_next_turn` AND `get_next_turns` wrappers
  (`mcp_server/server.py`), after calling the shared service, build the lean shape
  with `_lean_payload_for_mcp(payload)` that drops ONLY the heavy duplicates:
  top-level `strategy`, and `static.rules` / `static.base_prompt` /
  `static.your_strategy` (via `dict.pop(k, None)`, no KeyError).
- KEEP all live state — top-level `your_private_state` (hidden dice — Liar's Dice
  breaks without it), `public_state`, `history`, `scoreboard`, `current`, tokens,
  status — and every other `static` key, especially `static.coach_note` (live
  per-round coaching) plus `total_rounds`/`turns_per_round`/`your_agent_id`/
  `all_agent_ids`.
- A test pins the exact MCP `static` key set (tripwire vs future bloat) and
  asserts `your_private_state` + `coach_note` survive when present.
- Tests: MCP `get_next_turn` payload has no `base_prompt`/`rules` anywhere and no
  duplicated strategy; connector `/agent/next-turn` payload STILL has
  `base_prompt` + `rules` (regression); `get_next_turns` is also stripped.
- Est: ~70 lines.

### Slice 3 — cut 4 tools, fix prompt + docs  `[CHECKPOINT]`
- Remove `get_turn`, `get_standings`, `get_turn_detail`, `get_opponent_history`
  from `mcp_server/server.py` (and any now-unused imports/helpers they alone used
  — verify nothing else references them).
- Update `mcp_server/README.md` tool table and `tests/test_mcp.py` tool-list
  assertions to the 7 tools.
- Sync `docs/setup-mcp.md` — the `_PLAY_PROMPT` code comment says it MUST stay in
  sync with that doc. Update the doc's play-prompt block and tool list to match
  the rewritten kickoff prompt and the 7-tool surface, so published setup
  instructions don't drift from runtime (Codex plan MEDIUM #4).
- Rewrite `_PLAY_PROMPT` in `app/routes/connections_connect_guide.py` (kickoff):
  one bold loop rule; call `get_instructions` first (re-call if rules lost);
  distinct `waiting` vs `no_game`+`should_stop` handling; tokens once; multi-agent
  fan-out demoted; catch-up points only at `get_chat`; KEEP the pinned opening
  sentence "You are playing Hoard Hurt Help through the agentludum MCP tools.".
  Update `tests/test_connection_management.py` play-prompt assertions accordingly.
- Est: ~120 lines.

## Test plan

- New `tests/test_mcp_instructions.py` (or add to `test_mcp.py`): Slice-1 cases.
- Extend `test_mcp.py`: tool-list == the 7 names; MCP `get_next_turn` /
  `get_next_turns` payloads omit `base_prompt`/`rules`.
- New/extended connector regression: `/agent/next-turn` payload still includes
  `base_prompt` + `rules` (assert the shared builder output is unchanged).
- Preflight: `python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q`.

## Residual Risks

- **Strip misses a nested key, leaking duplicated text on MCP.**
  verification: a test asserts the serialized MCP `get_next_turn` payload contains
  neither the string "RESPONSE FORMAT" / "Return exactly one JSON object" nor a
  `base_prompt`/`rules` key at any depth, while the connector payload does.
- **Identity-helper extraction subtly changes the connector payload.**
  verification: a regression test compares the connector `/agent/next-turn`
  payload's `static` block keys + `base_prompt`/`rules`/`strategy` presence
  against the pre-change expectation (must be unchanged).
- **`get_instructions` picks the wrong agent for a multi-agent user.**
  verification: a test with two active agents asserts `get_instructions(agent_id=X)`
  returns X's strategy and identity, not the other agent's.
- **Removing a tool breaks an unrelated caller/test.**
  verification: full `pytest -q` green; `grep -rn "get_turn\b\|get_standings\|get_turn_detail\|get_opponent_history" tests/ app/ mcp_server/` shows no live references after the cut.


## Tasks to implement (your scope)
- [ ] T1.1 Add `agent_identity_for(db, connection, *, agent_id=None, match_id=None) -> (match, your_agent_id, all_agent_ids, strategy_text)` in the engine. Resolve from the user's ACTIVE (non-archived) agents and their live/scheduled matches — NOT `_collect_candidates` (claimable open turns only), so it works at idle / before a turn opens. Reuse the agent/version + seat-name derivation (`agent_play_next_turn.py:279–298`). Don't change `_build_turn_payload`'s connector output.
- [ ] T1.2 Add `semantic_rules_text(total_rounds, turns_per_round) -> str` to `GameModule` (`app/games/base.py`, base default) — rules WITHOUT `RESPONSE_PROTOCOL`. Implement HHH (`hoard_hurt_help/game.py`, reuse `make_game_rules_text`) and Liar's Dice (`liars_dice/game.py`, its `make_game_rules_text`). NEVER use `rules_text()`/`make_rules_text`/`agent_base_prompt`.
- [ ] T1.3 Add `get_instructions` MCP tool in `mcp_server/server.py`: optional `agent_id`/`match_id`; resolve via `_resolve_oauth_connection` + `agent_identity_for`; resolve the match's `GameModule`. Assemble `## The rules` (=`module.semantic_rules_text(...)`), `## You`, `## Your strategy` (`strategy_text`), `## How to answer` (GENERIC MCP block: talk→`submit_talk`, act→`submit_action`, call the tool not JSON). Selection: agent_id→that agent (match_id or its most-urgent match); no selector+one agent→use it; +multiple→rules+how-to-answer+agent_id list; no active game→short "no active game yet; start one then call get_instructions again" note WITHOUT rules sections. Game-agnostic for rules — no hardcoded HHH.
- [ ] T1.4 Tests (new `tests/test_mcp_instructions.py`): sections present; how-to-answer names the tools and contains neither "JSON" nor "RESPONSE FORMAT"; strategy section carries `strategy_text`; two active agents → `get_instructions(agent_id=X)` returns X's; **Liar's Dice agent → Liar's-Dice rules, not HHH**; **no active game → no-game note, no rules section**.
- [ ] T1.5 Verify: `ruff`, `mypy`, `pytest -q` green. Connector regression untouched.
- [ ] T2.1 Add `_lean_payload_for_mcp(payload: dict) -> dict` in `mcp_server/server.py`: drop ONLY top-level `strategy` and `static.rules`/`static.base_prompt`/`static.your_strategy` (via `dict.pop(k, None)`, no KeyError). KEEP all live state: top-level `your_private_state`, `public_state`, `history`, `scoreboard`, `current`; and every other `static` key, especially `coach_note`.
- [ ] T2.2 Apply it in the MCP `get_next_turn` AND `get_next_turns` wrappers after calling the shared service.
- [ ] T2.3 Tests: MCP `get_next_turn` payload has no `base_prompt`/`rules`/duplicate strategy; the lean `static` key set equals the expected pinned set; `your_private_state` and `coach_note` SURVIVE when present (Liar's Dice / coached turn); same for `get_next_turns`; connector `/agent/next-turn` payload STILL contains `base_prompt` + `rules` (regression).
- [ ] T2.4 Verify: `ruff`, `mypy`, `pytest -q` green.
- [ ] T3.1 Remove `get_turn`, `get_standings`, `get_turn_detail`, `get_opponent_history` from `mcp_server/server.py`; drop any imports/helpers used ONLY by them (grep to confirm).
- [ ] T3.2 Update `mcp_server/README.md` tool table and `tests/test_mcp.py` tool-list assertions to exactly: `get_instructions`, `get_next_turn`, `get_next_turns`, `submit_talk`, `submit_action`, `get_chat`, `get_game_state`.
- [ ] T3.3 Rewrite `_PLAY_PROMPT` in `app/routes/connections_connect_guide.py`: one bold loop rule (keep calling `get_next_turn`; never pause/ask; stop only on a turn or `should_stop`); call `get_instructions` first (re-call if rules lost); distinct `waiting` vs `no_game`+`should_stop`; tokens once; multi-agent fan-out at the end; catch-up → `get_chat` only; KEEP opening sentence "You are playing Hoard Hurt Help through the agentludum MCP tools.".
- [ ] T3.4 Update `tests/test_connection_management.py` play-prompt assertions (no refs to removed tools; pinned opening sentence intact).
- [ ] T3.5 Sync `docs/setup-mcp.md` — update its play-prompt block + tool list to match the rewritten `_PLAY_PROMPT` and the 7-tool surface (the code comment requires they stay in sync).
- [ ] T3.6 Verify: `ruff`, `mypy`, `pytest -q` green; `grep -rn "get_turn\b\|get_standings\|get_turn_detail\|get_opponent_history" app/ mcp_server/ tests/` shows no live references.

## File scope
(no specific scope — implement all tasks)

Implement ONLY the tasks listed above for this slice. Do not implement tasks from other slices and do not work ahead. Commit your changes when done.
DO NOT MODIFY: CLAUDE.md, AGENTS.md, MEMORY.md, the docs/ design/architecture docs, or any file outside this slice's declared scope. The spec/plan above are context only — they describe the whole feature, not your slice; build just the tasks listed.
