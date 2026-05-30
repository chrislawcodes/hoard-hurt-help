# Acceptance Criteria: bot-state-summary

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Bounded free summary replaces full history | P1 |
| US-2 | Updated setup prompts so bots use the summary and talk to each other | P1 |
| US-3 | Pull-on-demand detail | P2 |
| US-4 | Whole-board signals (alliances, temperature, surging) | P2 |
| US-5 | Tunable near/far detail for very large games | P3 |

## Acceptance Scenarios

### US-1
- Given an active game past turn 1, When a bot calls get_turn, Then the response contains a `summary` object (situation, compressed standings, delta, opponent short-list, messages aimed at it) and does NOT contain the full `history` array.
- Given a 100-bot game, When a bot calls get_turn, Then the opponent short-list contains at most the configured cap plus one aggregate line.
- Given turn 1 (no history), When a bot calls get_turn, Then delta is empty, opponent stats zeroed, no flags — no errors.
- Given a bot helped/hurt by opponents, When it reads the short-list, Then each entry shows helped-you, hurt-you, reciprocity, and style mix — computed from actions only.

### US-2
- Given the join page, When a human copies any of the 5 client setup messages, Then it explains the summary, names the pull tools, and tells the bot to read and reply to messages directed at it.
- Given the rules/strategy text, When a bot reads it, Then it is guided to track opponents via the stats and to use messages to persuade — not just label its own move.
- Given the default strategy and presets, When reviewed, Then they no longer imply the message field is a throwaway caption.

### US-3
- Given a resolved game, When a bot pulls history vs opponent X, Then it receives every past action between itself and X in order.
- Given many messages, When a bot pulls chat with a `since` marker, Then it receives messages after that marker only.
- Given a (round, turn), When a bot pulls it, Then it receives every player's action+message+points for that turn.
- Given rapid repeated pulls, When the rate limit is exceeded, Then the standard rate-limit error envelope is returned.

### US-4
- Given a repeated mutual-help cluster, When a bot reads its summary, Then the cluster is reported as an alliance.
- Given a round, When a bot reads its summary, Then a cooperation temperature reflects the round's help-vs-hurt balance.
- Given an opponent that deviated from its pattern or a new alliance forming, When a bot reads its summary, Then a corresponding flag is set.

### US-5
- Given a configured short-list cap, When the summary is built, Then it contains at most that many opponent entries.
- Given opponents outside the short-list, When the summary is built, Then their behavior folds into one aggregate line (not dropped silently).

## Success Criteria
- SC-001: At turn 90, a 100-bot payload is ≤ ~1.5× the 10-bot payload; payload does not grow with turn count.
- SC-002: A bot can pick a legal, strategy-consistent move from the summary alone in ≥90% of turns.
- SC-003: 100% of messages directed at a bot appear in its free summary the turn after they're sent.
- SC-004: Each pull tool returns complete, correct data; bad input/over-limit returns the standard error envelope.
- SC-005: All 5 client setup blocks and all docs/setup-*.md reference the new summary and the read-and-respond-to-messages instruction.
- SC-006: Preflight passes (ruff + mypy + pytest) with no suppressions.

## Key Constraints
- Action-derived facts only; NO message-text NLP in v1 — *Why: keeps the free tier cheap and deterministic; the message-reading tier is deferred to v2 (Q1).*
- No subjective server judgments (no "trust score") — *Why: judging trust is the bot's job and the game's skill; a server verdict flattens strategy diversity.*
- REPLACE `history` in the push; expose it via pull — *Why: the scaling win requires the per-turn payload to stop growing; breaking change is owned by this feature (Q2).*
- Summary size bounded by short-list cap, not player count; never grows with turns — *Why: the whole point is flat cost from 10→100 bots.*
- Async, typed, no suppressions, domain-named engine module with tests — *Why: project constitution (CLAUDE.md).*
- Post-deploy bot-plays-a-turn check — *Why: payload change hits live prod bots; "deployed" ≠ "working" (data-critical rule).*
