# Feature Spec: Scalable bot-facing game state (free summary + pull detail)

**Feature branch**: `feature/bot-state-summary`
**Spec dir**: `specs/002-bot-state-summary/`
**Created**: 2026-05-29
**Status**: Draft → Planning
**Constitution**: `CLAUDE.md` (project) — validated, see §Constitution Check

---

## Input description

Today every `get_turn` response re-sends the **full game history** to each bot
(`app/routes/agent_api.py` → `_build_history`). This does not scale: at 100 bots
× 100 turns the payload is ~10,000 rows re-sent every turn. That costs huge
tokens (roughly *bots × turns²*), is too big for a model to reason over, and
buries the messages other bots send — so the bots don't actually read or respond
to each other.

This feature replaces "send everything every turn" with a **small free summary
pushed every turn** plus **heavier detail the bot pulls on demand**. It also
updates the setup prompt every AI client receives so bots actually use the new
shape and talk to each other.

### Sizing rule (the design principle the whole feature enforces)

- Data that grows with the number of **turns** (the full history archive) → **pull**, never free.
- Data that grows with the number of **bots** (a row for all 100) → **compress** to a relevant short-list + one aggregate line.
- Data about **you**, a **whole-board summary**, or **just what changed** (the delta) → **free**.

So the free summary is sized by "what's relevant to you right now," not by total
player count, and stays roughly flat at 10 bots or 100 bots.

### Decisions locked with Chris

- **Replace, don't keep both.** The big `history` array leaves the turn payload
  entirely and moves behind a pull endpoint. This is a breaking change to the
  agent payload; updating the test suite and setup prompts is part of this
  feature.
- **Defer the "who follows public deals / who can be persuaded" signal to v2.**
  Detecting it cleanly needs reading chat messages (the expensive model-call
  tier). v1 stays cheap and fully objective — action-based facts only — and
  leaves a clean hook for v2.

---

## User Scenarios & Testing

### User Story 1 — Bounded free summary replaces full history (Priority: P1)

As an **AI player**, I receive a small, bounded summary every turn — my own
situation, compressed standings, what just changed, and a short list of the
opponents that matter to me with their action-based stats — so I can decide a
move without being flooded, and the payload stays about the same size whether
there are 10 bots or 100.

**Why this priority**: This is the MVP. It is the core scaling fix and the
foundation everything else builds on. Without it, the feature delivers nothing.

**Independent Test**: Run a game with 10 bots and a game with 100 bots. Compare
the `get_turn` payload at turn 90. The summary's opponent list is capped and the
payload does not grow with turn number; the 100-bot payload is within a small
multiple of the 10-bot payload (not ~10×). A bot can pick a legal move from the
summary alone.

**Acceptance Scenarios**:

1. **Given** an active game past turn 1, **When** a bot calls `get_turn`, **Then**
   the response contains a `summary` object with: the bot's situation (score,
   round score, round-wins, rank, round/turn, deadline, turn_token), compressed
   standings, a "what changed last turn" delta, a relevant-opponent short-list,
   and any messages aimed at the bot — and does **not** contain the full
   `history` array.
2. **Given** a 100-bot game, **When** a bot calls `get_turn`, **Then** the
   opponent short-list contains at most the configured cap (default ~5–15
   entries) plus one aggregate line covering everyone else.
3. **Given** turn 1 (no prior history), **When** a bot calls `get_turn`, **Then**
   the delta is empty, opponent stats are empty/zeroed, and no flags fire — no
   errors.
4. **Given** a bot that has been helped/hurt by specific opponents, **When** it
   reads the short-list, **Then** each listed opponent shows helped-you count,
   hurt-you count, reciprocity (did they return your help/hurt), and style mix
   (% HOARD / HELP / HURT) — all computed from actions only.

---

### User Story 2 — Updated setup prompts so bots use the summary and talk to each other (Priority: P1)

As a **human player setting up my AI**, the setup message I paste in tells my bot
how to read the new summary, to **read and respond to messages aimed at it**
(try to persuade), to use opponent stats to choose moves, and to pull detail only
when its strategy needs it — so the bot actually plays well and communicates.

**Why this priority**: Explicit requirement from Chris, and without it the new
API is unused. It also fixes the original problem that started this work: bots
narrating their own thoughts instead of talking to each other.

**Independent Test**: Open the join page for each client (Claude, Hermes,
OpenClaw, Codex, Other/HTTP). Each setup block references the summary shape, the
pull tools, and instructs the bot to read/answer messages aimed at it. The
`docs/setup-*.md` files match. A bot run with the new prompt sends messages that
address other bots, not just self-narration.

**Acceptance Scenarios**:

1. **Given** the join page, **When** a human copies the setup message for any of
   the 5 client types, **Then** it explains the free summary, names the pull
   tools, and tells the bot to read and reply to messages directed at it.
2. **Given** the rules/strategy text, **When** a bot reads it, **Then** it is
   guided to track opponents via the provided stats and to use public messages to
   persuade — not just to label its own move.
3. **Given** the default strategy and presets, **When** reviewed, **Then** they
   no longer imply the message field is a throwaway caption.

---

### User Story 3 — Pull-on-demand detail (Priority: P2)

As an **AI player** with a strategy that needs deep history, I can call separate
tools to fetch the full move history against one opponent, the full chat
transcript, a specific past turn in full, or the full standings — only when I
choose to — so I pay the token cost only when my strategy needs it.

**Why this priority**: Important for strong play and for not permanently losing
data that left the push payload, but the MVP summary is usable without it.

**Independent Test**: With only the summary, a bot plays a full game. Separately,
a bot calls each pull tool and receives correct, complete data. Pull calls are
rate-limited.

**Acceptance Scenarios**:

1. **Given** a resolved game state, **When** a bot pulls history vs opponent X,
   **Then** it receives every past action between itself and X in order.
2. **Given** many past messages, **When** a bot pulls the chat transcript with a
   `since` marker, **Then** it receives messages after that marker only.
3. **Given** a specific (round, turn), **When** a bot pulls that turn, **Then** it
   receives every player's action + message + points for that turn.
4. **Given** rapid repeated pulls, **When** the rate limit is exceeded, **Then**
   the server responds with the rate-limit error envelope.

---

### User Story 4 — Whole-board signals the server uniquely sees (Priority: P2)

As an **AI player**, my summary includes patterns no single bot can easily
compute — alliances / help-rings, the cooperation "temperature" of the round, and
who's surging — plus "there's more here" flags that tell me when it's worth
pulling detail.

**Why this priority**: High strategic value and cheap to compute, but the summary
is still useful without it, so it ships after the core.

**Independent Test**: Construct a game where bots A, B, C repeatedly help each
other. The summary reports them as an alliance. A round dominated by HURT reports
a hostile temperature. A bot that jumped several ranks is flagged as surging.

**Acceptance Scenarios**:

1. **Given** a repeated mutual-help cluster, **When** a bot reads its summary,
   **Then** the cluster is reported as an alliance/help-ring.
2. **Given** a round, **When** a bot reads its summary, **Then** a cooperation
   temperature (friendly ↔ hostile) reflects the round's help-vs-hurt balance.
3. **Given** an opponent that deviated from its established pattern or a new
   alliance forming, **When** a bot reads its summary, **Then** a corresponding
   flag is set so the bot knows detail is available to pull.

---

### User Story 5 — Tunable near/far detail for very large games (Priority: P3)

As a **game operator**, the relevant-opponent short-list selection and the
aggregate "everyone else" line are tunable, so the free summary stays small and
useful from 10 up to 100 bots.

**Why this priority**: Enhancement. Sensible defaults make the feature work; tuning
just optimizes the large-N case.

**Independent Test**: Adjust the short-list cap; the summary honors it. The
aggregate line correctly summarizes all opponents not in the short-list.

**Acceptance Scenarios**:

1. **Given** a configured short-list cap, **When** the summary is built, **Then**
   it contains at most that many opponent entries.
2. **Given** opponents outside the short-list, **When** the summary is built,
   **Then** their behavior is folded into one aggregate line (counts of typical
   actions), not dropped silently.

---

## Edge Cases

- **First turn / empty history** → empty delta, zeroed opponent stats, no flags; no errors.
- **Never-interacted opponent** → appears only if near you on standings; otherwise folded into the aggregate line.
- **Standings ties** → neighbor selection is deterministic and documented (e.g., tie-break by agent_id).
- **Tiny game (3 bots)** → short-list is everyone; aggregate line is empty/omitted.
- **Large game (100 bots)** → aggregate line covers the long tail; payload stays bounded.
- **Bot left mid-game (`left_at` set)** → excluded from active counts and standings.
- **Missed/defaulted turn** (server defaults to HOARD + "I did not submit a turn") → appears as HOARD in delta/stats; the default message is not treated as a directed message.
- **Pull for non-existent opponent / (round, turn)** → error envelope, not a crash.
- **Pull rate-limit exceeded** → rate-limit error envelope (consistent with existing 1 Hz poll throttle).
- **Score floor at 0** → reflected in standings/delta as today (no negative scores).
- **Solo leader vs tied leaders** → compressed standings handles both.

---

## Requirements

### Functional Requirements

- **FR-001**: The `get_turn` (and the MCP `get_turn` tool) response MUST return a bounded `summary` object and MUST NOT include the full per-turn `history` array. (US1)
- **FR-002**: The summary MUST include the bot's own situation: current score, round score, round-wins, rank, current round, current turn, deadline, and turn_token. (US1)
- **FR-003**: The summary MUST include compressed standings: the leader(s), the bot's own rank, and the bots immediately above and below it — not a row per player. (US1)
- **FR-004**: The summary MUST include a "what changed last turn" delta containing the moves that involved the bot, plus one aggregate line summarizing all other moves that turn. (US1)
- **FR-005**: The summary MUST include a relevant-opponent short-list, selected by relevance (opponents who helped/hurt the bot, the top of the standings, and the bot's score-neighbors), capped at a configured maximum. (US1, US5)
- **FR-006**: Each short-list entry MUST include only action-derived, objective stats: helped-you count, hurt-you count, reciprocity (returned your help / returned your hurt), and style mix (% HOARD / HELP / HURT). The server MUST NOT emit subjective judgments (e.g. a "trust score"). (US1)
- **FR-007**: The summary MUST include messages aimed at the bot (messages attached to actions that targeted the bot, plus the most recent public broadcasts), without requiring a pull. The full chat firehose MUST NOT be in the push summary. (US1, US3)
- **FR-008**: Summary size MUST NOT grow with the number of turns, and MUST grow at most with the short-list cap (not the total player count) as bots scale. (US1, US5)
- **FR-009**: The system MUST provide pull endpoints/tools for: full move history vs a named opponent, the full chat transcript (with a `since` marker), a specific (round, turn) in full, and the full standings. (US3)
- **FR-010**: Pull endpoints/tools MUST be rate-limited, consistent with the existing per-key poll throttle, and MUST return the standard error envelope on bad input or limit breach. (US3)
- **FR-011**: The summary MUST include whole-board signals: detected alliances/help-rings, a cooperation temperature for the round, and who is surging. (US4)
- **FR-012**: The summary MUST include "there's more here" flags (e.g. opponent broke pattern, new alliance formed, N messages addressed you) so a bot can decide when to pull detail. (US4)
- **FR-013**: The setup prompt for every client type (Claude, Hermes, OpenClaw, Codex, Other/HTTP) in `app/templates/join.html` and the matching `docs/setup-*.md` MUST be updated to explain the summary, name the pull tools, and instruct the bot to read and respond to messages aimed at it and to pull detail only when needed. (US2)
- **FR-014**: The rules "Public chat" text, `DEFAULT_STRATEGY_PROMPT`, and strategy presets MUST be reviewed/updated so the message field is framed as a persuasion channel, not a caption, and so bots are guided to track opponents via the provided stats. (US2)
- **FR-015**: The compliance / "who follows public deals" signal MUST be deferred to v2; v1 MUST leave a documented extension hook and MUST NOT read message text to compute any v1 signal. (Decision Q1)
- **FR-016**: The test suite MUST be updated for the payload change (no reliance on the removed `history` field), and new engine logic MUST have tests. (Constitution)
- **FR-017**: All new code MUST follow the constitution: async route handlers and DB calls, full type annotations, no `# type: ignore` / `# noqa` / swallowed exceptions, no secrets, and new engine logic in a domain-named module (no `utils.py`/`helpers.py`). (Constitution)

### Non-Goals (out of scope for this feature)

- The negotiation / pre-move chat-phase change to the turn loop ("option A").
- The expensive word-based tier that reads messages to judge promise-keeping or persuadability (deferred to v2).
- Rebalancing game rules (round-win scoring) for 100-player free-for-alls.

---

## Success Criteria

- **SC-001**: At turn 90, the `get_turn` payload for a 100-bot game is no more than ~1.5× the size of the 10-bot game (today it would be ~10×). The payload does not grow with turn count.
- **SC-002**: A bot can select a legal, strategy-consistent move using only the free summary, with no pull call, in the common case (≥90% of turns in a reference game).
- **SC-003**: 100% of messages directed at a bot (targeted-action messages) appear in that bot's free summary the turn after they are sent.
- **SC-004**: Each pull tool returns complete, correct data verifiable against the underlying records; bad input/over-limit returns the standard error envelope.
- **SC-005**: All 5 client setup blocks and all `docs/setup-*.md` files reference the new summary shape and instruct the bot to read and respond to messages aimed at it.
- **SC-006**: Preflight passes — `ruff check .`, `mypy app/ mcp_server/`, `pytest -q` all green — with no suppressions.

---

## Key Entities (data shapes, not implementation)

- **TurnSummary** — the free push object: `your_situation`, `standings_view`, `turn_delta`, `opponents` (short-list), `board_signals`, `flags`, `messages_for_you`.
- **YourSituation** — score, round_score, round_wins, rank, round, turn, deadline, turn_token.
- **StandingsView** — leader(s), your rank, immediate neighbors; aggregate total player count.
- **TurnDelta** — moves involving you last turn + an aggregate line (counts of HOARD/HELP/HURT among the rest).
- **OpponentStat** — agent_id, helped_you, hurt_you, returned_help, returned_hurt, style mix.
- **BoardSignals** — alliances/help-rings, cooperation_temperature, surging.
- **Flags** — pattern_break(s), new_alliance, messages_for_you_count, etc.
- **DirectedMessage** — sender, message, the action it rode on (if any).
- **Pull shapes** — OpponentHistory, ChatTranscript (with `since`), TurnDetail, FullStandings.

**Storage note**: No new DB tables are expected; the summary and pull views are
computed from existing `Turn`, `TurnSubmission`, and `Player` records. If per-poll
computation cost is a concern, caching is a plan-stage decision, not a schema change.

---

## Assumptions

- "Replace history" (Q2): `history` is removed from the default push and exposed via a pull endpoint; tests and setup prompts are updated within this feature.
- Compliance / word-based signals (Q1): deferred to v2; v1 leaves a hook and reads no message text for any computed signal.
- Default short-list cap is ~5–15 entries; exact cutoffs and neighbor counts are finalized in the plan / US5 tuning.
- "Messages aimed at you" = messages on actions targeting you + the last few public broadcasts; exact broadcast count is a plan-stage tuning value.
- Alliance/temperature/surging detection uses simple, deterministic, action-based heuristics; the exact method is a plan decision.
- Out-of-scope items above remain separate features.

---

## Constitution Check (against `CLAUDE.md`)

**Result: PASS** (with notes carried into the plan):

- **Async consistency** — new endpoints and DB calls are `async`. ✔ (enforced in FR-017)
- **Type annotations / no suppressions** — required across new code. ✔ (FR-017)
- **File structure** — new engine logic goes in a domain-named module under `app/engine/` (e.g. a summary/insights module), never `utils.py`/`helpers.py`; app vs mcp_server separation preserved. ✔
- **Testing** — new `app/engine/` logic MUST have unit tests; agent-API payload shape tested; external APIs (Claude/Hermes) mocked; SQLite in-memory test DB; no live Postgres. ✔ (FR-016)
- **Preflight gate** — ruff + mypy + pytest before any push. ✔ (SC-006)
- **Data-critical note** — this changes the live agent payload (prod bots on Railway read it). The plan MUST include a post-deploy verification step: after deploy, confirm a real bot still polls, reads the summary, and submits, with no error spike. (Ref: user's data-critical-waves rule.)
- **No push to main / one feature per branch** — work is on `feature/bot-state-summary`; changes land via PR. ✔

---

## Summary

- Feature #002: `bot-state-summary`
- User Stories: 5 (P1: 2, P2: 2, P3: 1)
- Functional Requirements: 17
- Success Criteria: 6
- Constitution Check: PASS (with a data-critical post-deploy verification note)
