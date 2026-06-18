# Tasks: MCP play prompt + tools cleanup

Three checkpoint-bounded slices, in order (Slice 3 depends on Slice 1's
`get_instructions` existing, since the kickoff prompt points at it). Each slice
ends at a `[CHECKPOINT]`: build + tests + commit, then a diff review.

Connector path must stay byte-for-byte unchanged throughout — never edit the
shared `_build_turn_payload` output shape for the connector, the `/agent/next-turn`
route, or `RESPONSE_PROTOCOL`.

---

## Slice 1 — game-agnostic `get_instructions` tool + identity helper  `[CHECKPOINT]`
Est: ~220 lines. Deps: none.

- [ ] T1.1 Add `agent_identity_for(db, connection, *, agent_id=None, match_id=None) -> (match, your_agent_id, all_agent_ids, strategy_text)` in the engine. Resolve from the user's ACTIVE (non-archived) agents and their live/scheduled matches — NOT `_collect_candidates` (claimable open turns only), so it works at idle / before a turn opens. Reuse the agent/version + seat-name derivation (`agent_play_next_turn.py:279–298`). Don't change `_build_turn_payload`'s connector output.
- [ ] T1.2 Add `semantic_rules_text(total_rounds, turns_per_round) -> str` to `GameModule` (`app/games/base.py`, base default) — rules WITHOUT `RESPONSE_PROTOCOL`. Implement HHH (`hoard_hurt_help/game.py`, reuse `make_game_rules_text`) and Liar's Dice (`liars_dice/game.py`, its `make_game_rules_text`). NEVER use `rules_text()`/`make_rules_text`/`agent_base_prompt`.
- [ ] T1.3 Add `get_instructions` MCP tool in `mcp_server/server.py`: optional `agent_id`/`match_id`; resolve via `_resolve_oauth_connection` + `agent_identity_for`; resolve the match's `GameModule`. Assemble `## The rules` (=`module.semantic_rules_text(...)`), `## You`, `## Your strategy` (`strategy_text`), `## How to answer` (GENERIC MCP block: talk→`submit_talk`, act→`submit_action`, call the tool not JSON). Selection: agent_id→that agent (match_id or its most-urgent match); no selector+one agent→use it; +multiple→rules+how-to-answer+agent_id list; no active game→short "no active game yet; start one then call get_instructions again" note WITHOUT rules sections. Game-agnostic for rules — no hardcoded HHH.
- [ ] T1.4 Tests (new `tests/test_mcp_instructions.py`): sections present; how-to-answer names the tools and contains neither "JSON" nor "RESPONSE FORMAT"; strategy section carries `strategy_text`; two active agents → `get_instructions(agent_id=X)` returns X's; **Liar's Dice agent → Liar's-Dice rules, not HHH**; **no active game → no-game note, no rules section**.
- [ ] T1.5 Verify: `ruff`, `mypy`, `pytest -q` green. Connector regression untouched.

**Verification at checkpoint:** `agent_identity_for` works with no open turn (idle); the connector `_build_turn_payload` output is unchanged; a Liar's Dice agent's `get_instructions` contains Liar's-Dice rules and no HHH actions; no-active-game returns the note without rules.

---

## Slice 2 — lean MCP per-turn payload  `[CHECKPOINT]`
Est: ~70 lines. Deps: none (independent of Slice 1).

- [ ] T2.1 Add `_lean_payload_for_mcp(payload: dict) -> dict` in `mcp_server/server.py`: drop ONLY top-level `strategy` and `static.rules`/`static.base_prompt`/`static.your_strategy` (via `dict.pop(k, None)`, no KeyError). KEEP all live state: top-level `your_private_state`, `public_state`, `history`, `scoreboard`, `current`; and every other `static` key, especially `coach_note`.
- [ ] T2.2 Apply it in the MCP `get_next_turn` AND `get_next_turns` wrappers after calling the shared service.
- [ ] T2.3 Tests: MCP `get_next_turn` payload has no `base_prompt`/`rules`/duplicate strategy; the lean `static` key set equals the expected pinned set; `your_private_state` and `coach_note` SURVIVE when present (Liar's Dice / coached turn); same for `get_next_turns`; connector `/agent/next-turn` payload STILL contains `base_prompt` + `rules` (regression).
- [ ] T2.4 Verify: `ruff`, `mypy`, `pytest -q` green.

**Verification at checkpoint:** serialized MCP payload has neither "Return exactly one JSON object" nor "RESPONSE FORMAT"; connector payload still has them.

---

## Slice 3 — cut 4 tools, rewrite kickoff prompt, docs  `[CHECKPOINT]`
Est: ~120 lines. Deps: Slice 1 (prompt references `get_instructions`).

- [ ] T3.1 Remove `get_turn`, `get_standings`, `get_turn_detail`, `get_opponent_history` from `mcp_server/server.py`; drop any imports/helpers used ONLY by them (grep to confirm).
- [ ] T3.2 Update `mcp_server/README.md` tool table and `tests/test_mcp.py` tool-list assertions to exactly: `get_instructions`, `get_next_turn`, `get_next_turns`, `submit_talk`, `submit_action`, `get_chat`, `get_game_state`.
- [ ] T3.3 Rewrite `_PLAY_PROMPT` in `app/routes/connections_connect_guide.py`: one bold loop rule (keep calling `get_next_turn`; never pause/ask; stop only on a turn or `should_stop`); call `get_instructions` first (re-call if rules lost); distinct `waiting` vs `no_game`+`should_stop`; tokens once; multi-agent fan-out at the end; catch-up → `get_chat` only; KEEP opening sentence "You are playing Hoard Hurt Help through the agentludum MCP tools.".
- [ ] T3.4 Update `tests/test_connection_management.py` play-prompt assertions (no refs to removed tools; pinned opening sentence intact).
- [ ] T3.5 Sync `docs/setup-mcp.md` — update its play-prompt block + tool list to match the rewritten `_PLAY_PROMPT` and the 7-tool surface (the code comment requires they stay in sync).
- [ ] T3.6 Verify: `ruff`, `mypy`, `pytest -q` green; `grep -rn "get_turn\b\|get_standings\|get_turn_detail\|get_opponent_history" app/ mcp_server/ tests/` shows no live references.

**Verification at checkpoint:** full suite green; removed tools have zero live references; kickoff prompt keeps the pinned sentence.

---

## Parallel analysis
Slices 1 and 2 touch disjoint code (Slice 1: engine helper + new tool; Slice 2:
MCP wrappers strip) and could run in parallel, but Slice 3 depends on Slice 1.
Given the small total size, run sequentially 1 → 2 → 3 for simpler checkpoints.
