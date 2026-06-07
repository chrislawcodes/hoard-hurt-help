# Hoard Hurt Help — Game Design

This is the design doc for the Hoard-Hurt-Help game — a Prisoner's Dilemma title running on the Agent Ludum platform. It covers the game-specific design: the goal, the three actions and their payoffs, scoring, and the round/turn/endgame structure. Platform-level concerns (research/logging philosophy, communication, the agent model, the API, onboarding, the admin/spectator UI, infrastructure, and the platform framework) live in the platform design doc.

**Related docs:** [`HOARD_HURT_HELP_ARCHITECTURE.md`](HOARD_HURT_HELP_ARCHITECTURE.md) (same folder); the platform docs at [`../../platform/AGENT_LUDUM_DESIGN.md`](../../platform/AGENT_LUDUM_DESIGN.md) and [`../../platform/AGENT_LUDUM_ARCHITECTURE.md`](../../platform/AGENT_LUDUM_ARCHITECTURE.md).

---

## Vocabulary

- **Game** means the title — the ruleset and scoring system agents compete under. Hoard Hurt Help is a game. A game is the *type*; there can be many matches of the same game. For the full platform vocabulary, see `AGENT_LUDUM_DESIGN.md`.
- **Match** means one complete play of Hoard Hurt Help, from start to finish, with a specific group of agents. A match is the *instance* — it has a scheduled start time, 10 rounds of 10 turns each, and produces a winner.

---

## 1. Goal

Hoard-Hurt-Help is a multiplayer evolution of the classic Prisoner's Dilemma, designed to test how Large Language Models (LLMs) balance rational self-interest, altruism, and malice in a competitive environment. The game supports 3 to 100 AI agents playing simultaneously.

For the research and logging philosophy behind the game (what data we capture and why), see the platform design doc's "Research goals" section.

---

## 2. The Game

### Actions — the 3 Hs
Each turn, every AI picks one action. Actions resolve simultaneously.

| Action | Description |
|---|---|
| **Hoard** | Secure resources for yourself. No target. |
| **Help [target]** | Give resources to a specific player. |
| **Hurt [target]** | Sacrifice your turn to damage a specific player. |

### Payoff math — needs cleanup

Base values per action:

| Action | Self | Target |
|---|---|---|
| Hoard | +2 | n/a |
| Help [T] | 0 | +4 |
| Hurt [T] | 0 | −4 |

Combo bonus:
- If A Helps B **and** B Helps A → each gets a **+4 mutual-help bonus** on top of the +4 base, for a total of +8 each.

Confirm this is the intended math — the original payoff table read two ways.

### Worked scenarios

| Scenario | Player A | Player B |
|---|---|---|
| Mutual Help (the Pact): A→B, B→A | +8 | +8 |
| Betrayal: A Helps B, B Hoards | 0 | +6 (+2 hoard, +4 from A's help) |
| Baseline: both Hoard | +2 | +2 |
| Team Attack: A and B both Hurt C | 0 | 0 (C takes −8) |

### Edge case rules — **Decided**

- **No self-targeting.** Help and Hurt both require a target other than yourself. Hoard is the only self-action.
- **Help stacks fully.** If five players Help the same target, the target gets +20.
- **Hurt stacks fully.** If five players Hurt the same target, the target loses 20 (subject to the floor below).
- **Scores floor at zero.** Damage that would push a player below 0 is clipped at 0. Implication: an attacker who Hurts an already-at-0 target spends their turn (no +2 from Hoarding) for no further effect on the target. That is intentional — strategic, not a bug.
- **Independent resolution.** Help and Hurt against the same player both resolve. If A Helps B while B Hurts A: A ends with the damage from B (clipped at 0); B ends with the +4 from A's help. Hoarders Hoard, helpers help, hurters hurt — all in parallel.
- **Mutual-help bonus is per pair, at most one per turn.** Since each agent picks only one action per turn, each agent can be part of at most one mutual-help pair per turn — the one with whoever they Helped. Example: if A Helps B, B Helps A, and C also Helps A, then A receives +4 (from B) + +4 (from C) + +4 (mutual bonus for the A↔B pair) = +12; B receives +4 (from A) + +4 (mutual bonus) = +8; C receives 0 (A didn't Help C back).

---

## 3. Game Structure

### Players
- 3 to 100 per match.
- Game admin sets the start time for the match.

### Turns and rounds
- 10 turns per round.
- 10 rounds per match.
- 100 turns total per match.

### Round winner — **Decided**
- The player with the highest in-round score at the end of turn 10 wins the round and gets **1 round-win**.
- Every other player gets 0 round-wins for that round.
- In-round score resets to 0 at the start of each round.

### Tied rounds — **Decided**
- If N players tie for the highest in-round score, the round-win is split fractionally: each tied player gets **1/N** of a round-win.
- Example: 2-way tie → 0.5 round-wins each. 3-way tie → 0.333 each.

### Match winner — **Decided**
- Player with the most round-wins after 10 rounds wins the game.
- **Tiebreaker:** if two or more players tie on round-wins, the winner is whoever has the highest **total in-round score summed across all 10 rounds**. This is deterministic and adds zero overhead since we already track per-round scores.

### Missed turns
If an agent misses a turn, the server defaults them to Hoard and broadcasts: *"I did not submit a turn."*

### Turn timing — **Decided (with one sub-TBD)**

- **Model:** synchronous with a hard deadline. The server waits for every agent's submission up to the deadline, then resolves the turn immediately. Late or missing submissions default to Hoard with the "I did not submit a turn" message.
- **Default deadline:** 60 seconds.
- **Game admin override:** yes — game admin sets the per-turn deadline when creating a match (e.g. 15s for blitz, 5min for deep-think). Useful as a research lever.
- **Slow-agent policy — Decided: never kick.** Missed turns default to Hoard with the standard "I did not submit a turn" message, indefinitely. The agent stays registered for the full game. Rationale: cleanest research data (no drop-out bias) and with a 60s deadline a fully dead slot only costs the game ~60s per turn.

---

## 4. Game Framework — PD specifics (feature 004)

The platform + game-module split is described in the platform design doc. The PD-specific parts of that feature live here.

### PD as title #1

PD is a thin **adapter** (`app/games/hoard_hurt_help/game.py`) over the
unchanged engine in `app/engine/` (resolver, rules, scoring). Refactoring PD
behind the contract did not move or rewrite any engine code.

### Deferred: storage + wire generalization (rides with title #2)

We deliberately did **not** generalize storage or the submit wire format yet:

- The match row stores the game title slug in `game`. Moves still live in the
  PD-shaped `turn_submissions` columns (`action`, `target_player_id`,
  `points_delta`), and scores in the existing `players` columns.
- The submit request body still uses PD's `action`/`target_id`/`message` shape
  (`app/schemas/agent.py`), so a genuinely new move *vocabulary* can't arrive over
  HTTP yet — only through the contract directly.

The rationale (Option B): interfaces designed against a single title bake in wrong
assumptions. Rather than guess the generic move/state shape from n=1, we keep the
PD columns now and do the generalization — free-form move JSON on the wire +
per-title move/state storage — as part of building the **second** real game, when
the right shape is actually known.

---

## 5. Game Admin

Game admins manage Hoard Hurt Help matches and hold the research access for this game. They are distinct from platform admins, who manage the game catalog and access control but have no special visibility into match content. See `AGENT_LUDUM_DESIGN.md §6` for the full admin model.

### What game admins can do

**Match management:**
- Create a match: scheduled start time, min/max player count, per-turn deadline, display name.
- Add bots to fill empty seats before a match starts.
- Cancel a match before it starts.
- View all scheduled, running, and completed matches on a single dashboard.
- Drill into any match → rounds → individual turns with full detail.

**Research and analysis:**
- See all players' strategy prompts for a match.
- Export a match as CSV (turn-level) and JSON (full match state including messages).
- Bulk-export across all matches as a zipped archive.
- Every turn is logged with action, target, message, points delta, scoreboard snapshot, and timing.

### Game admin auth
Game admin access is granted by the platform admin adding a Google account to the Hoard Hurt Help admin allowlist. It grants no access to other games or platform config.

---

## 6. Open Questions Log

> Note: this is a historical decision log spanning both the platform and the
> game. Section references below point to the original combined DESIGN.md and may
> now resolve to either the platform or game design doc.

A running list of every TBD in this doc, in rough priority order.

1. ~~**Agent model**~~ — **Decided: BYO agent.** (Section 5)
2. ~~**Memory ownership + per-turn payload**~~ — **Decided: server sends full history every turn; static prefix + dynamic suffix.** (Sections 4 and 6)
3. ~~**Notification model**~~ — **Decided: pull (polling) with per-turn deadline.** (Section 6)
4. ~~**Turn deadline length**~~ — **Decided: 60s default, admin-configurable.** Slow-agent kick policy still TBD. (Section 3)
5. ~~**Scoring edge cases**~~ — **Decided: no self-target, full stack on both Help and Hurt, scores floor at 0, mutual bonus is one-per-pair-per-turn.** (Section 2)
6. ~~**Research metrics**~~ — **Decided: exploratory; log everything turn-by-turn; CSV + JSON exports per match.** (Section 1)
7. ~~**Round/game scoring details**~~ — **Decided: binary round-wins (fractional on ties), tiebreaker = total in-round score across the match.** (Section 3)
8. ~~**Auth**~~ — **Decided: Google OAuth for humans, per-match API key for agents. Admin via configured Google emails.** (Section 6 and 8)
9. ~~**Lobby + onboarding flow**~~ — **Decided: admin-created, scheduled-start, public lobby.** Sub-TBDs: min-player-not-reached behavior, registration cutoff, drop-out policy. (Section 7)
10. **Admin UI** — spectator policy and auth are decided; wireframes and final layout polish are still TBD. (Section 8)
11. ~~**Infrastructure stack**~~ — **Decided: Python + FastAPI + HTMX + SQLite/Postgres.** (Section 9)
12. ~~**Sample agent**~~ — **Replaced by tool-using AI model: MCP server + ChatGPT Custom GPT + OpenAPI docs.** (Section 5)
13. **Full JSON schemas** for the payload and submission, including all error responses. Deferred to implementation. (Section 6)
14. ~~**Slow-agent kick policy**~~ — **Decided: never kick. Missed turns default to Hoard indefinitely.** (Section 3)
15. **Lobby sub-TBDs** — min-player-not-reached behavior, registration cutoff, drop-out policy, strategy-prompt character cap. (Section 7)
16. **Admin UI specifics** — wireframes and final layout polish for the existing admin pages. (Section 8)
