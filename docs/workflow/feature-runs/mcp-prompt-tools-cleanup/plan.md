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
