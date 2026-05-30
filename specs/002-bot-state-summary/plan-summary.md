# Plan Summary: bot-state-summary

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/engine/opponent_stats.py` | create | per-opponent tallies (helped_you/hurt_you/reciprocity/style) + short-list selection; constants MAX_SHORTLIST/TOP_THREATS/NEIGHBOR_RADIUS |
| `app/engine/board_signals.py` | create | alliances (mutual-help components), cooperation temperature, surging, pattern-break flag |
| `app/engine/turn_summary.py` | create | assemble TurnSummary (situation, standings_view, delta, opponents+aggregate, signals, flags, messages_for_you) |
| `app/schemas/agent.py` | modify | add TurnSummary + sub-shapes + pull shapes; YourTurnResponse → {status, static, summary} (drop dynamic/history) |
| `app/routes/agent_api.py` | modify | build summary in GET /turn; add 4 pull endpoints; pull rate-limit dependency; keep _build_history logic behind the pull endpoint |
| `mcp_server/server.py` | modify | add get_opponent_history/get_chat/get_turn_detail/get_standings tools; update get_turn + submit_action docstrings to describe summary + persuasion |
| `app/engine/rules.py` | modify | "Public chat" text, DEFAULT_STRATEGY_PROMPT, STRATEGY_PRESETS — persuasion + opponent-tracking framing |
| `app/templates/join.html` | modify | 5 setup blocks (Claude, Hermes, OpenClaw, Codex, Other) reference summary + pull tools + read/respond to messages |
| `docs/setup-claude.md`, `setup-hermes.md`, `setup-codex.md`, `setup-openclaw.md`, `setup-other.md` | modify | match the join.html prompts |
| `tests/test_opponent_stats.py` | create | short-list selection + tallies + reciprocity |
| `tests/test_board_signals.py` | create | alliances/temperature/surging/pattern-break |
| `tests/test_turn_summary.py` | create | assembled shape + edge cases (turn 1, tiny/large game, left players, defaulted turns) |
| `tests/test_agent_api.py` | modify | new payload shape (replaces dynamic.turn_token assert); pull endpoints + rate limit |
| `tests/test_mcp.py` | modify | new pull tools; updated get_turn shape |

## Migration Steps

**No DB migration.** Operational steps:
1. Update tests to stop reading the removed `history`/`dynamic` field.
2. Update the 5 setup prompts + docs + MCP tool docstrings (part of the feature).
3. Preflight: `ruff check . && mypy app/ mcp_server/ && pytest -q`.
4. Post-deploy: verify one real/test bot polls prod, gets `summary`, submits a move; watch logs ~10 min (data-critical).

## Data Model

**None (no schema change).** All shapes computed on read from existing `Turn`,
`TurnSubmission`, `Player`. v2 hook: resolve-time denormalized interaction
counters if read cost is too high at 100 bots (no contract change needed).

## Key Constraints

- **Action-only, no message NLP (v1)**: computed signals use only HOARD/HELP/HURT + target, never message text — *Why: keeps free tier cheap/deterministic; message-reading deferred to v2 (Q1).*
- **No subjective scores**: server emits facts, not "trust" — *Why: trust judgment is the bot's job; a server verdict flattens strategy diversity.*
- **Replace history, expose via pull**: drop `history` from the push payload — *Why: per-turn payload must stop growing for the 10→100 bot scaling win (Q2).*
- **Bounded by short-list cap, never by turns**: summary size is O(MAX_SHORTLIST), independent of turn count and ~independent of player count — *Why: flat cost is the whole feature.*
- **Deterministic heuristics**: every selection/signal has explicit tie-breaks/thresholds — *Why: stable output, non-flaky tests.*
- **SQL GROUP BY aggregation, short-list-only detail**: don't recreate the O(N·T) blow-up on the server — *Why: moves cost off tokens without moving it onto server CPU.*
- **Domain-named engine modules + unit tests; async; typed; no suppressions** — *Why: CLAUDE.md constitution.*
- **Post-deploy bot-plays-a-turn check** — *Why: live payload change; data-critical rule.*
