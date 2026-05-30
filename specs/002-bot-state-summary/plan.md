# Implementation Plan: Scalable bot-facing game state (free summary + pull detail)

**Branch**: `feature/bot-state-summary` | **Date**: 2026-05-29 | **Spec**: [spec.md](./spec.md)

## Summary

Replace the full-history block in the `get_turn` payload with a small, bounded
**TurnSummary** computed on read from existing tables, add **pull** endpoints/MCP
tools for the heavy detail that left the push, and update every client setup
prompt so bots use the summary and actually talk to each other. No DB schema
change in v1.

---

## Technical Context

**Language/Version**: Python 3.11+ (async)
**Framework**: FastAPI + Starlette; MCP via `mcp.server.fastmcp` (FastMCP)
**ORM/DB**: SQLAlchemy 2.x async; Postgres (prod, Railway), SQLite in-memory (tests)
**Schemas**: Pydantic v2
**HTTP client**: httpx (MCP tools call our own HTTP API)
**Testing**: pytest (+ async); test DB is SQLite in-memory
**Target Platform**: Railway single-instance container
**Performance Goals** (from spec SC): payload at 100 bots ≤ ~1.5× the 10-bot payload (SC-001); payload does not grow with turn count; bot plays ≥90% of turns from the summary alone (SC-002)
**Constraints**: action-derived facts only, no message NLP in v1 (FR-015); no subjective scores (FR-006); async + typed + no suppressions (FR-017)
**Scale/Scope**: 3–100 bots/game, 10 rounds × 10 turns = 100 turns

---

## Constitution Check

**Status: PASS** (validated against `CLAUDE.md`)

### Python standards
- [x] Async route handlers and DB calls — all new endpoints `async def`
- [x] Full type annotations on every new signature; `from __future__ import annotations` where needed
- [x] No `# type: ignore` / `# noqa` / swallowed exceptions; specific exceptions only
- [x] No secrets

### File structure
- [x] New engine logic in **domain-named** modules under `app/engine/` — `opponent_stats.py`, `board_signals.py`, `turn_summary.py`. No `utils.py`/`helpers.py`.
- [x] App vs `mcp_server` separation preserved (MCP tools call the HTTP API, as today)

### Testing
- [x] New `app/engine/` logic gets unit tests (FR-016) — pure functions over fixture data
- [x] Agent-API payload shape + pull endpoints tested; external APIs mocked; SQLite in-memory
- [x] No live Postgres required for pytest

### Delivery
- [x] Feature branch + PR; preflight gate (ruff + mypy + pytest) before push (SC-006)
- [x] **Data-critical**: changes live agent payload → post-deploy verification step included (see Quickstart §Post-Deploy)

---

## Architecture Decisions

### Decision 1: Three focused engine modules, pure functions

**Chosen**: Split the computation by responsibility:
- `app/engine/opponent_stats.py` — per-opponent action tallies (helped_you, hurt_you, reciprocity, style mix) and the relevant-opponent short-list selection.
- `app/engine/board_signals.py` — alliances/help-rings, cooperation temperature, surging.
- `app/engine/turn_summary.py` — assembles the `TurnSummary` (situation, compressed standings, delta, opponents, signals, flags, messages-for-you) from the above.

**Rationale**: Constitution says keep files focused and split by responsibility.
Pure functions that take already-loaded rows (submissions, players) and return
Pydantic/dataclass results are trivially unit-testable without a DB, matching the
"test engine logic" rule. The route does the DB I/O, then calls these.

**Alternatives**: one big `summary.py` (rejected — does too many things);
computing inline in the route (rejected — untestable, violates engine-logic-has-tests).

**Tradeoffs**: Pro: testable, cohesive. Con: a little more wiring between modules.

### Decision 2: Compute on read in v1; denormalized counters deferred to v2

**Chosen**: Build the summary on each `get_turn` by loading the game's resolved
submissions and aggregating in **SQL `GROUP BY`** (not Python loops) where
possible, and building full per-opponent detail **only for the short-list**.

**Rationale**: `get_turn` is already rate-limited to 1 Hz per key. For ≤100 bots
× ≤100 turns (~10k rows) a grouped query per poll is acceptable, and avoids a
schema change. Premature optimization is discouraged.

**v2 hook**: if profiling shows read cost is too high at 100 bots, add
resolve-time denormalized tallies (a per-(player, opponent) interaction counter
updated in `resolver.resolve_turn`), making reads O(short-list). Documented so
it can slot in without reshaping the API.

**Tradeoffs**: Pro: no migration, simplest correct version. Con: repeated
recompute; mitigated by SQL aggregation + short-list-only detail.

### Decision 3: Payload shape — keep `static`, replace `dynamic` with `summary`

**Chosen**: `YourTurnResponse` becomes `{ status, static, summary }`.
- `static` (rules, rules_version, agent ids, your_strategy) stays — unchanged.
- `dynamic` (history + scoreboard + turn_token + deadline) is **removed** and its
  still-needed fields move into `summary.your_situation` (turn_token, deadline,
  round, turn) and `summary.standings_view`.

**Rationale**: Decision Q2 (replace, not keep both). `static` is genuinely
constant and the prompt already treats it as such.

**Note (out of scope, flagged for v2)**: `static.rules` re-sends ~1KB of constant
rules every turn. Trimming it (send once, reference by `rules_version`) is a
separate cost win; not in this feature to keep blast radius contained.

**Tradeoffs**: Pro: smallest change that delivers the scaling win. Con: breaking
change — `test_agent_api.py:205` (`dynamic.turn_token`) and `test_mcp.py` update
within this feature.

### Decision 4: Short-list selection (deterministic, action-based)

**Chosen** — an opponent enters the short-list if **any** of:
1. It interacted with you last resolved turn (it helped/hurt you, or you it).
2. It is in the top `TOP_THREATS` (default 3) by `current_round_score`.
3. It is a score-neighbor: within `NEIGHBOR_RADIUS` (default 2) ranks of you.
4. It is flagged this turn (pattern break, or it addressed you).

Union, then cap at `MAX_SHORTLIST` (default 12), keeping by priority order
1→4 and tie-breaking by `agent_id` (deterministic). Everyone not selected is
folded into one `aggregate` line (counts of HOARD/HELP/HURT among the rest, and
how many bots it covers). Constants live in `app/engine/opponent_stats.py`.

**Rationale**: Bounds the list by relevance, not by N (FR-005, FR-008). Fully
deterministic → testable and stable across polls.

### Decision 5: Board signals (deterministic, action-based, windowed)

**Chosen**:
- **Alliances/help-rings**: over the current round's resolved turns, build a
  directed help graph (edge weight = # of HELPs A→B). A mutual edge exists when
  A→B and B→A both ≥ `ALLY_MIN_HELPS` (default 2). Report connected components of
  the mutual-help graph (pairs and small clusters), capped at `MAX_ALLIANCES`.
- **Cooperation temperature**: over the current round, `help / (help + hurt)` →
  a 0–1 number plus a label (hostile <0.33, mixed, cooperative >0.66).
- **Surging**: players whose rank improved by ≥ `SURGE_RANK_JUMP` (default 2)
  over the last `SURGE_WINDOW` (default 3) turns, or top round-score gainer;
  report top `MAX_SURGING` (default 2).

All from `TurnSubmission` actions; **no message text** (FR-015). Constants in
`app/engine/board_signals.py`.

**Rationale**: These are the "whole-board" facts a single bot can't cheaply see
(US4). Simple, explainable heuristics keep them testable and cheap.

### Decision 6: Messages for you (no NLP)

**Chosen**: `summary.messages_for_you` = messages from the **last resolved turn**
on submissions whose `target_player_id == you` (these are structurally "aimed at
you"), plus the most recent `MAX_BROADCASTS` (default 5) public messages from that
turn. The `messages_for_you_count` flag counts the directed ones. The
server-defaulted "I did not submit a turn." message is excluded from directed
messages. No parsing of message content.

**Rationale**: Keeps the persuasion channel alive (US1/US2) at constant cost,
without entering the v2 message-reading tier.

### Decision 7: Pull endpoints + MCP tools (opt-in, rate-limited)

**Chosen** — new agent-authenticated REST endpoints under
`/api/games/{game_id}`, each mirrored by an MCP tool:

| REST | MCP tool | Returns |
|------|----------|---------|
| `GET /history/opponents/{opponent_id}` | `get_opponent_history` | every past action between you and that opponent, in order |
| `GET /chat?since={round}.{turn}` | `get_chat` | messages after the cursor, paginated |
| `GET /turns/{round}/{turn}` | `get_turn_detail` | all players' action+message+points for that turn |
| `GET /standings` | `get_standings` | full standings, all players |

Rate-limit: a small reusable per-key limiter (reusing the `_last_poll`/1 Hz
pattern, separate bucket for pulls) returning the standard `RATE_LIMITED`
envelope (FR-010). Bad opponent/turn → `INVALID_TARGET`/`NOT_FOUND` envelope.

**Rationale**: Cost follows curiosity (US3). Mirrors the existing
HTTP-API-behind-MCP pattern in `mcp_server/server.py`.

### Decision 8: No DB migration

**Chosen**: everything is computed from existing `Turn`, `TurnSubmission`,
`Player`. The "migration" work is: update tests, update the 5 setup prompts +
docs, update MCP tool docstrings, and the rules/strategy text.

---

## Project Structure

```
app/
├── engine/
│   ├── opponent_stats.py     [NEW] per-opponent tallies + short-list selection
│   ├── board_signals.py      [NEW] alliances, temperature, surging
│   ├── turn_summary.py       [NEW] assemble TurnSummary from the above
│   └── rules.py              [MODIFY] Public-chat text, DEFAULT_STRATEGY_PROMPT, presets
├── schemas/
│   └── agent.py              [MODIFY] add TurnSummary + sub-shapes + pull shapes; change YourTurnResponse
├── routes/
│   └── agent_api.py          [MODIFY] build summary in /turn; add 4 pull endpoints; pull rate-limit dep
└── templates/
    └── join.html             [MODIFY] 5 setup blocks reference summary + pull tools + messaging

mcp_server/
└── server.py                 [MODIFY] add 4 pull tools; update get_turn/submit_action docstrings

docs/
├── setup-claude.md  setup-hermes.md  setup-codex.md  setup-openclaw.md  setup-other.md   [MODIFY]

tests/
├── test_opponent_stats.py    [NEW] short-list + tallies unit tests
├── test_board_signals.py     [NEW] alliances/temperature/surging unit tests
├── test_turn_summary.py      [NEW] assembled summary shape + edge cases
├── test_agent_api.py         [MODIFY] new payload shape; pull endpoints
└── test_mcp.py               [MODIFY] pull tools; updated docstrings/shape
```

**Structure Decision**: Single FastAPI app (`app/`) + co-hosted MCP (`mcp_server/`).
This feature touches the agent-facing slice only; the web/spectator routes and
the `scoreboard.html` fragment (separate view) are untouched.

---

## Risks & Mitigations

- **Breaking payload in prod** → update setup prompts in the same PR; post-deploy verification (Quickstart §Post-Deploy); bots are instructed to read `summary`.
- **Read cost at 100 bots** → SQL `GROUP BY` aggregation + short-list-only detail; v2 denormalized-counter hook documented (Decision 2).
- **Non-deterministic summaries causing flaky tests** → all selection/signal heuristics are deterministic with explicit tie-breaks (Decisions 4–5).

---

## Constitution Compliance (references)

- Python standards, file structure, testing, preflight, and one-feature-per-branch per `CLAUDE.md`.
- Data-critical payload change per the user's data-critical-waves rule → post-deploy verification in Quickstart.
