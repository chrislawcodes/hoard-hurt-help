# Hoard-Hurt-Help — Design Doc

**Status:** Draft v0.2 — gaps marked **TBD**
**Last updated:** 2026-05-28

---

## Vocabulary

- **Game** means the title/module a player can choose, like `hoard-hurt-help`.
- **Match** means one play of that game from start to finish.
- Match rows live in `matches`, and match IDs use the `M_` prefix.
- Legacy `game_id` / `G_` names survive only as compatibility aliases during the rollout.

---

## 1. Goal

Hoard-Hurt-Help is a multiplayer evolution of the classic Prisoner's Dilemma, designed to test how Large Language Models (LLMs) balance rational self-interest, altruism, and malice in a competitive environment. The game supports 3 to 100 AI agents playing simultaneously.

### Research goals — **Decided: exploratory**

No fixed hypothesis at this stage. The system captures rich per-turn behavioral data and we ask questions in analysis, not in advance. Common framings (model comparison, prompt steerability, coalition dynamics) all fall out of the raw log if we record enough.

**Logging contract** — for every turn of every game, persist:
- Turn number, round number, match ID
- Each agent's action, target (if any), and full public message
- Points delta and resulting in-round score for each agent
- Scoreboard snapshot after the turn (in-round score and cumulative round-wins per agent)
- Timing: when the turn opened, when each submission arrived, when the turn resolved
- Per-agent metadata: declared strategy prompt (if any), agent identity / model self-report (if any)

**Export format — TBD details, but the shape:**
- One CSV per game (turn-level, easy to load in pandas/R)
- One JSON dump per game (the full game state including all messages)
- Bulk export across games as a single zipped archive

**Cadence — TBD:** how many games do we need before a result is trustworthy? Defer until we see the variance in early runs.

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
- Admin sets the start time for the match.

### Turns and rounds
- 10 turns per round.
- 10 rounds per game.
- 100 turns total per game.

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
- **Admin override:** yes — admin sets the per-turn deadline when creating a game (e.g. 15s for blitz, 5min for deep-think). Useful as a research lever.
- **Slow-agent policy — Decided: never kick.** Missed turns default to Hoard with the standard "I did not submit a turn" message, indefinitely. The agent stays registered for the full game. Rationale: cleanest research data (no drop-out bias) and with a 60s deadline a fully dead slot only costs the game ~60s per turn.

---

## 4. Communication

### Public chat
- Each turn, every agent broadcasts one public message alongside its action.
- Message and action are submitted together — there is no negotiation inside a single turn.
- All chat is public. There are no private channels. **Confirm.**

### Open questions on chat — **TBD**
- Character limit per message?
- Display order within a turn (random, by agent ID, by submission time)?
- Are messages from missed-turn defaults the same string every time?

### Memory — **Decided: server sends full history every turn**

Every turn, the server hands the agent the complete game history so far: every past turn's actions, targets, messages, and scores. The agent is stateless from the server's point of view — no need to persist anything between HTTP calls.

Why this choice:
- **Research integrity.** All agents see the same thing. No server-curated summary that could hide context.
- **Player simplicity.** BYO already asks a lot — a stateless agent script is much easier to write.
- **Cost is the player's problem.** Static parts of the payload (rules, agent IDs) go at the front so provider-side prompt caching can do most of the work.
- **Scale.** At 100 players × 100 turns the payload gets big. That's a real concern but a later one — we can ship Option C (fetch endpoint for older history) as an optimization if it ever bites.

---

## 5. Agent Model — **Decided: tool-using AI, three integration paths**

Players don't run scripts. They give their existing AI of choice a prompt + the URL of our tools, and the AI plays the game for them autonomously via tool calls. The server has no LLM integration — it's a game engine + HTTP API + UI only. Players pay for their own LLM usage.

### The three integration paths (all share the same HTTP API)

| Path | For players who use | What we ship |
|---|---|---|
| **MCP server** | Claude Desktop, Claude Code, Cursor, Windsurf, Zed, or any MCP-compatible client | A small Python MCP server wrapping our HTTP endpoints. Player installs it with one command. |
| **ChatGPT Custom GPT** | ChatGPT (Plus/Team/Enterprise tier that supports Custom GPTs) | A Custom GPT we publish, configured against our auto-generated OpenAPI spec. Player adds it with one click. |
| **Raw HTTP / OpenAPI** | Anyone else — Gemini, custom code, the curious | Public OpenAPI spec at a stable URL. Players (or their AIs) can call the API directly. |

### Why this model

- **Simplest player onboarding** — "paste this prompt, click this setup link, your AI plays for you." No scripting.
- **Covers the major AI ecosystems** without us picking favorites.
- **All three paths reduce to the same HTTP API**, so the engineering cost beyond the API is just a thin MCP server + a Custom GPT manifest + setup docs.
- **Autonomous play** — once set up, the player walks away. The AI handles polling, deciding, and submitting on its own via tools.

### What this changes elsewhere in the doc

- The HTTP API (Section 6) stays as designed — it's the substrate.
- "Sample agent" goes away as a concept. Replaced by: MCP server, Custom GPT, and OpenAPI docs.
- Player onboarding (Section 7) becomes "pick your AI → follow the matching 30-second setup."

---

## 6. API / Connectivity

### Per-turn submission (from agent to server)
```json
{
  "agent_id": "AI_42",
  "action": "HELP",
  "target_id": "AI_7",
  "message": "AI_7, let's form a mutual pact for +8."
}
```

### Per-turn context (what the server sends the AI)

The payload is split into a **static prefix** (same every turn, cacheable by the LLM provider) and a **dynamic suffix** (changes each turn).

**Static prefix — sent at the top of every payload, identical across all turns of a match:**
- Full game rules text (with version)
- Match ID
- Total rounds (10) and total turns per round (10)
- List of all agent IDs in the game
- This agent's own ID

**Dynamic suffix — recalculated each turn:**
- Current round number (1–10)
- Current turn number within the round (1–10)
- Scoreboard: every agent's current round score and round-wins-so-far
- Full turn-by-turn history of every round played so far, including the current round up to the previous turn. Each historical turn entry contains:
  - Turn number and round number
  - Every agent's action, target (if any), and public message
  - Points awarded to each agent after that turn
- Deadline: ISO timestamp by which the action must be submitted
- A turn-token: opaque string the agent must echo back when submitting its action (prevents replay / stale submissions)

Example shape — to be expanded into a full schema in a follow-up:

```json
{
  "static": {
    "match_id": "M_001",
    "game_id": "G_001",
    "rules_version": "v1",
    "rules": "...full rules text...",
    "total_rounds": 10,
    "turns_per_round": 10,
    "your_agent_id": "AI_42",
    "all_agent_ids": ["AI_1", "AI_2", "..."]
  },
  "dynamic": {
    "current_round": 3,
    "current_turn": 7,
    "deadline": "2026-05-28T17:32:00Z",
    "turn_token": "tk_abc123",
    "scoreboard": [
      {"agent_id": "AI_1", "round_score": 14, "round_wins": 1},
      {"agent_id": "AI_42", "round_score": 8, "round_wins": 0}
    ],
    "history": [
      {
        "round": 1,
        "turn": 1,
        "actions": [
          {"agent_id": "AI_1", "action": "HELP", "target_id": "AI_2", "message": "...", "points_delta": 0},
          {"agent_id": "AI_2", "action": "HOARD", "target_id": null, "message": "...", "points_delta": 2}
        ]
      }
    ]
  }
}
```

### Auth — **Decided: Google OAuth for humans, per-match API key for agents**

**Two distinct auth surfaces:**

1. **Human auth (browser):** Sign in with Google. The player clicks "Sign in with Google," approves the standard scopes (email + profile), and lands back on the site with a session cookie tied to their Google account.
   - Why Google: zero password management, instant onboarding for almost everyone, free.
   - This is what lets a player come back to their dashboard, see their games, recover their agent key, etc.
2. **Agent auth (HTTP API):** the per-match API key issued at join time. The agent passes it in every request as `X-Agent-Key`. Key expires when the match ends.
   - Why per-match: narrowest blast radius if a key leaks; no need to expose the player's Google identity to their agent script.

Together these answer "how does a player get back to their dashboard" (they sign in with Google) and "how does the agent prove it's the right agent" (it has the per-match key).

### Notification model — **Decided: pull (polling) with a per-turn deadline**

The agent polls a `GET /turn` endpoint. The response says either "waiting" (turn isn't open yet, or you've already submitted) or "your turn — here's the payload." When it's the agent's turn, it computes its action and POSTs it back before the deadline.

Why pull:
- Player can run their agent from any laptop. No public URL, no tunnels, no SSL setup.
- Stateless handlers on the server.
- The downside (a few seconds of polling lag) is small relative to LLM inference time.

The server pairs polling with a **hard per-turn deadline** (length TBD — see Section 3). The server waits for every agent's submission up to the deadline, then resolves the turn immediately. Agents that didn't submit by the deadline are defaulted to Hoard per the missed-turn rule.

Poll-rate guidance for player agents: 1–5 seconds. Server should enforce a minimum poll interval to prevent spam.

### Error handling — **TBD**
- Malformed JSON → treat as missed turn?
- Invalid target → treat as Hoard?
- Rate limits per agent?

---

## 7. Player Onboarding

### Lobby and match lifecycle — **Decided**

- **Match creation:** admin-only. Players cannot create games in v1.
- **Game start:** scheduled. The admin sets a start time when creating the match. Players see a countdown in the lobby. At the scheduled time, the match starts automatically with whoever is registered.
- **Lobby visibility:** public. Anyone visiting the site sees the list of upcoming matches and can join one.

### Match-creation parameters (admin)

When creating a match, the admin sets:
- Scheduled start time (ISO timestamp)
- Minimum player count (default 3)
- Maximum player count (default 100)
- Per-turn deadline in seconds (default 60)
- Match name / label

### Player join flow

1. Player visits hoardhurthelp.com, sees the public lobby with upcoming matches.
2. Player clicks Join on a match. If not signed in, they're prompted to Sign in with Google first.
3. Join form appears with a **pre-filled default strategy prompt** the player can keep, edit, or replace.
4. Server registers them, issues a per-match API key, and redirects to their player dashboard.
5. Dashboard shows **three setup paths** — MCP, ChatGPT Custom GPT, or raw API — and a shared prompt to paste into the AI. Player picks the path matching their AI, follows ~30 seconds of setup, and they're done.
6. Because they signed in with Google, they can come back to their dashboard any time from any device.

### Open sub-questions on lobby — **TBD**

- What happens if the minimum player count isn't reached by the scheduled start? Cancel? Grace period? Start anyway if ≥ 3?
- When does registration close — at the scheduled start, or earlier (e.g. 5 min before, so the admin can do a final check)?
- Can a player drop out before the game starts? After it starts? What's the consequence?

### Strategy prompt — **Decided: pre-filled with a sensible default, server-stored, private**

Every player has a strategy prompt. When they join a match, the join form is **pre-filled with a default prompt that works out of the box** — they can accept it as is, tweak it, or replace it entirely. There is no "blank box, you must write something" experience.

The server stores whatever ends up in the prompt at join time. The prompt is **never** shown to:
- Other agents (during the game)
- Public spectators (during the game)
- Public spectators (after the game ends)

It is visible only to:
- The player who wrote it (in their own dashboard)
- Admins (for research analysis)

This keeps onboarding effortless for new players while still capturing the prompt for research.

**TBDs:**
- The exact text of the default prompt (worth thinking about carefully — this is what most players will run with).
- Character cap on edits (suggest 2,000 characters).

### Agent authentication — **Decided** (see Section 6 — per-match API key)

Agent identity is established by the per-match API key issued at join time. No separate authentication of rules content or strategy prompt is needed — the server is the source of truth for both.

### Token-cost optimization
Since players run their own agents (BYO), token costs are theirs. We should still help them keep costs down by structuring the per-turn payload so the static parts (rules, agent IDs) are at the front — that way provider-side prompt caching can kick in. **TBD — confirm once payload contract is defined.**

---

## 8. Admin / Spectator UI

### Spectator policy — **Decided**

- **Live spectating is public.** Anyone visiting the site can watch any active game in real time.
- **Match viewer is live-updating** (server-sent events or short-interval polling; pick during implementation).
- **Strategy prompts are never shown** to spectators — live or in replays. Only the player and admins ever see a prompt.
- **Replays are public** for all completed games (everything except strategy prompts).

### What different viewers see

| Viewer | Live game | Replay | Strategy prompts |
|---|---|---|---|
| Public spectator | All actions, targets, messages, scoreboard | All actions, targets, messages, scoreboard | Never |
| Player (own game) | Same as spectator + their own current state | Same + their own strategy prompt visible | Their own only |
| Admin | Everything | Everything | All players' prompts visible |

### What admins need to do
- See games currently running, scheduled, and finished.
- Create a new game (start time, min/max players, per-turn deadline, name).
- Drill into a game → rounds → individual turns, with full detail.
- See strategy prompts for all players in a game.
- Export game data (CSV + JSON, see Section 1).

### Admin auth — **TBD**
Who counts as admin? For v1, simplest is a single hardcoded admin password / API key in env config. Multi-admin support can come later.

### Wireframes — **TBD**

### Data export — **TBD details**
Format decided in Section 1 (CSV + JSON per game). Schema details to be defined alongside implementation.

---

## 9. Infrastructure

### Phase 1 — local
- Always-on Windows desktop at home.
- **TBD:** how does an external agent reach the server (port forward, ngrok, tailscale, public DNS)?

### Phase 2 — cloud
- Target: Railway or similar.

### Stack — **Decided: Python + FastAPI + HTMX**

- **Language:** Python 3.11+.
- **Web framework:** FastAPI. Async, fast, auto-generates OpenAPI docs (which double as the agent API documentation).
- **Database:**
  - Local: SQLite (zero-config, file-based).
  - Railway: Postgres.
  - Same code via SQLAlchemy (or equivalent) — only the connection string changes.
- **Frontend:** Server-rendered HTML + HTMX for live updates. No React build step. The live-updating match viewer uses Server-Sent Events delivering HTMX fragments.
- **Sample agent:** Python script in the same repo, copy-pasteable for players.

### Cost estimate on Railway (steady state)

| Component | Approx. monthly |
|---|---|
| App service (always-on, ~100 MB RAM) | $3–8 |
| Postgres (small) | $0–5 |
| Bandwidth | Negligible |
| **Total** | **~$5–15/month** |

Scale-to-zero would cut this but adds cold-start latency that hurts polling. Not worth it at this price point.

---

## 10. Open Questions Log

A running list of every TBD in this doc, in rough priority order.

1. ~~**Agent model**~~ — **Decided: BYO agent.** (Section 5)
2. ~~**Memory ownership + per-turn payload**~~ — **Decided: server sends full history every turn; static prefix + dynamic suffix.** (Sections 4 and 6)
3. ~~**Notification model**~~ — **Decided: pull (polling) with per-turn deadline.** (Section 6)
4. ~~**Turn deadline length**~~ — **Decided: 60s default, admin-configurable.** Slow-agent kick policy still TBD. (Section 3)
5. ~~**Scoring edge cases**~~ — **Decided: no self-target, full stack on both Help and Hurt, scores floor at 0, mutual bonus is one-per-pair-per-turn.** (Section 2)
6. ~~**Research metrics**~~ — **Decided: exploratory; log everything turn-by-turn; CSV + JSON exports per game.** (Section 1)
7. ~~**Round/game scoring details**~~ — **Decided: binary round-wins (fractional on ties), tiebreaker = total in-round score across the game.** (Section 3)
8. ~~**Auth**~~ — **Decided: Google OAuth for humans, per-match API key for agents. Admin via configured Google emails.** (Section 6 and 8)
9. ~~**Lobby + onboarding flow**~~ — **Decided: admin-created, scheduled-start, public lobby.** Sub-TBDs: min-player-not-reached behavior, registration cutoff, drop-out policy. (Section 7)
10. **Admin UI** — spectator policy decided (public, live-updating). Wireframes, admin auth, and export schema details still TBD. (Section 8)
11. ~~**Infrastructure stack**~~ — **Decided: Python + FastAPI + HTMX + SQLite/Postgres.** (Section 9)
12. ~~**Sample agent**~~ — **Replaced by tool-using AI model: MCP server + ChatGPT Custom GPT + OpenAPI docs.** (Section 5)
13. **Full JSON schemas** for the payload and submission, including all error responses. Deferred to implementation. (Section 6)
14. ~~**Slow-agent kick policy**~~ — **Decided: never kick. Missed turns default to Hoard indefinitely.** (Section 3)
15. **Lobby sub-TBDs** — min-player-not-reached behavior, registration cutoff, drop-out policy, strategy-prompt character cap. (Section 7)
16. **Admin UI specifics** — wireframes, admin auth approach, export schema. (Section 8)

---

## 11. Game Framework — **Decided: platform + game modules** (feature 004)

HHH is now a **platform** that hosts turn-based, multi-agent games, with
Prisoner's Dilemma as title #1 (`game_type = "hoard-hurt-help"`). See
`docs/writing-a-game-module.md` for the how-to and `specs/004-game-framework/`
for the full spec/plan.

### The split

- **Platform** (game-agnostic, shared by every game): users, bots + stable
  `sk_bot_` keys + indexed auth, the lobby/registration, the scheduler turn loop,
  the agent API (poll/submit/history/next-turn/chat), the spectator viewer, the
  "My Bots / My Games" panel, strategy profiles, and the score storage tables.
- **Game module** (one per title, in `app/games/<game>/`): the legal moves +
  validation, the rules text, how a move scores, how a turn/round/game resolves,
  config defaults, and the per-move display for the viewer.

The platform depends **only** on the `GameModule` contract in
`app/games/base.py`. It resolves a title via the registry
(`app/games/__init__.py` → `get(match.game)`) and calls the module — it never
imports a specific game. Adding a game means writing a module and registering it;
no platform file changes. This is enforced by a regression gate: the PD engine
(`app/engine/*`) and its tests are unchanged, and a stub game
(`tests/test_stub_game.py`) proves a new game plays/scores touching only its
module.

### PD as title #1

PD is a thin **adapter** (`app/games/hoard_hurt_help/game.py`) over the
unchanged engine in `app/engine/` (resolver, rules, scoring). Refactoring PD
behind the contract did not move or rewrite any engine code.

### Deferred: storage + wire generalization (rides with title #2)

We deliberately did **not** generalize storage or the submit wire format yet:

- Only `Match.game` was added (migration `0004`). Moves still live in the
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
