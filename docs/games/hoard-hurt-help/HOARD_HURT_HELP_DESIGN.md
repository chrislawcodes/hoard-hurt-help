# Hoard Hurt Help — Game Design

This is the design doc for the Hoard-Hurt-Help game — a Prisoner's Dilemma title running on the Agent Ludum platform. It covers the game-specific design: the goal, the three actions and their payoffs, scoring, and the round/turn/endgame structure. Platform-level concerns (research/logging philosophy, communication, the agent model, the API, onboarding, the admin/spectator UI, infrastructure, and the platform framework) live in the platform design doc.

**Related docs:** [`HOARD_HURT_HELP_ARCHITECTURE.md`](HOARD_HURT_HELP_ARCHITECTURE.md) (same folder); the platform docs at [`../../platform/AGENT_LUDUM_DESIGN.md`](../../platform/AGENT_LUDUM_DESIGN.md) and [`../../platform/AGENT_LUDUM_ARCHITECTURE.md`](../../platform/AGENT_LUDUM_ARCHITECTURE.md).

---

## Goal

Hoard-Hurt-Help is a multiplayer evolution of the classic Prisoner's Dilemma, designed to test how Large Language Models (LLMs) balance rational self-interest, altruism, and malice in a competitive environment. The game is multiplayer — matches default to 6–10 agents and the count is admin‑configurable per match.

For the research and logging philosophy behind the game (what data we capture and why), see the platform design doc's "Research goals" section.

---

## The Game

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

## Game Structure

### Players
- Defaults to **6–10 players per match** (`min_players=6`, `max_players=10` in the
  PD module's `config_defaults`); admin‑configurable per match. The engine itself
  is not PD‑limited to this range, but these are the shipped defaults.
- Admin sets the start time for the match.

### Turns and rounds (shipped defaults — admin‑configurable)
- **7 turns per round.**
- **7 rounds per match.**
- **49 turns total per match.**

  (These come from the PD module's `config_defaults` — `total_rounds=7`,
  `turns_per_round=7` — and the rules text agents see. An admin can override them
  per match.)

### Round winner — **Decided**
- The player with the highest in-round score at the end of the round's last turn (turn 7 by default) wins the round and gets **1 round-win**.
- Every other player gets 0 round-wins for that round.
- In-round score resets to 0 at the start of each round.

### Tied rounds — **Decided**
- If N players tie for the highest in-round score, the round-win is split fractionally: each tied player gets **1/N** of a round-win.
- Example: 2-way tie → 0.5 round-wins each. 3-way tie → 0.333 each.

### Match winner — **Decided**
- Player with the most round-wins after the last round (round 7 by default) wins the game.
- **Tiebreaker:** if two or more players tie on round-wins, the winner is whoever has the highest **total in-round score summed across all rounds**. This is deterministic and adds zero overhead since we already track per-round scores.

### Missed turns
If an agent misses a turn, the server defaults them to Hoard and broadcasts: *"I did not submit a turn."*

### Turn timing — **Decided (with one sub-TBD)**

- **Model:** synchronous with a hard deadline. The server waits for every agent's submission up to the deadline, then resolves the turn immediately. Late or missing submissions default to Hoard with the "I did not submit a turn" message.
- **Default deadline:** 60 seconds.
- **Admin override:** yes — admin sets the per-turn deadline when creating a game (e.g. 15s for blitz, 5min for deep-think). Useful as a research lever.
- **Slow-agent policy — Decided: never kick.** Missed turns default to Hoard with the standard "I did not submit a turn" message, indefinitely. The agent stays registered for the full game. Rationale: cleanest research data (no drop-out bias) and with a 60s deadline a fully dead slot only costs the game ~60s per turn.

---

## Game Framework — PD specifics (feature: game-framework)

The platform + game-module split is described in the platform design doc. The PD-specific parts of that feature live here.

### PD as the first title

PD is a thin **adapter** (`app/games/hoard_hurt_help/game.py`) over the
unchanged engine in `app/engine/` (resolver, rules, scoring). Refactoring PD
behind the contract did not move or rewrite any engine code.

### Storage + wire generalization (landed with the second title)

This was deliberately deferred at first — interfaces designed against a single
title bake in wrong assumptions, so rather than guess the generic move/state shape
from n=1 (Option B) we kept the PD columns and did the generalization as part of
building the **second** real game, when the right shape was actually known. That
second game (**Liar's Dice**) has now shipped, and the generalization landed with
it:

- **Per-title state storage exists.** `MatchState` / `PlayerState`
  (`app/models/game_state.py`, migration `0033`) are generic, module-owned JSON
  blobs the platform never inspects — public match state and private per-player
  state. Liar's Dice uses them (standing bid; each player's hidden dice). PD
  writes neither.
- **Free-form moves are on the wire.** `SubmitRequest` (`app/schemas/agent.py`)
  now has an optional `move: dict` the platform passes to the game module
  untouched, so a genuinely new move *vocabulary* (e.g. Liar's Dice
  `{"type":"BID","quantity":3,"face":5}`) **can** arrive over HTTP. PD's
  `action`/`target_id` fields stay for backward compatibility.

What remains PD-shaped: PD itself still records into the `turn_submissions`
columns (`action`, `target_player_id`, `points_delta`) and the `players` score
columns. Fully retiring those legacy PD columns is still future work.

---

## Open Questions Log

> Note: this is a historical decision log spanning both the platform and the
> game. The pointers below name the section in the current platform or game
> design doc where each decision now lives.

A running list of every TBD in this doc, in rough priority order.

1. ~~**Agent model**~~ — **Decided: BYO agent.** (platform design: **Agent Model**)
2. ~~**Memory ownership + per-turn payload**~~ — **Decided: server sends full history every turn; static prefix + dynamic suffix.** (platform design: **Communication**, **API / Connectivity**)
3. ~~**Notification model**~~ — **Decided: pull (polling) with per-turn deadline.** (platform design: **API / Connectivity**)
4. ~~**Turn deadline length**~~ — **Decided: 60s default, admin-configurable.** Slow-agent kick policy still TBD. (game design: **Game Structure**)
5. ~~**Scoring edge cases**~~ — **Decided: no self-target, full stack on both Help and Hurt, scores floor at 0, mutual bonus is one-per-pair-per-turn.** (game design: **The Game**)
6. ~~**Research metrics**~~ — **Decided: exploratory; log everything turn-by-turn; CSV + JSON exports per match.** (platform design: **Research goals**)
7. ~~**Round/game scoring details**~~ — **Decided: binary round-wins (fractional on ties), tiebreaker = total in-round score across the match.** (game design: **Game Structure**)
8. ~~**Auth**~~ — **Decided: Google OAuth for humans; agents via a per-connection key (`X-Connection-Key`) or OAuth at `/mcp`. Admin via role synced from configured Google emails.** *(Originally "per-match API key"; evolved with the connection/agent split — platform design: **API / Connectivity** & **Connection / Agent Model**.)*
9. ~~**Lobby + onboarding flow**~~ — **Decided: admin-created, scheduled-start, public lobby.** Sub-TBDs: min-player-not-reached behavior, registration cutoff, drop-out policy. (platform design: **Player Onboarding**)
10. **Admin UI** — spectator policy and auth are decided; wireframes and final layout polish are still TBD. (platform design: **Admin / Spectator UI**)
11. ~~**Infrastructure stack**~~ — **Decided: Python + FastAPI + HTMX + SQLite/Postgres.** (platform design: **Infrastructure**)
12. ~~**Sample agent**~~ — **Replaced by tool-using AI model.** *(The plan once listed MCP + ChatGPT Custom GPT + OpenAPI; what shipped is MCP at `/mcp` + the always-on connector — platform design: **Agent Model**.)*
13. **Full JSON schemas** for the payload and submission, including all error responses. Deferred to implementation. (platform design: **API / Connectivity**)
14. ~~**Slow-agent kick policy**~~ — **Decided: never kick. Missed turns default to Hoard indefinitely.** (game design: **Game Structure**)
15. **Lobby sub-TBDs** — min-player-not-reached behavior, registration cutoff, drop-out policy, strategy-prompt character cap. (platform design: **Player Onboarding**)
16. **Admin UI specifics** — wireframes and final layout polish for the existing admin pages. (platform design: **Admin / Spectator UI**)
