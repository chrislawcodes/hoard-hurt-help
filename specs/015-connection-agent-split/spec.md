# Feature 015 — Connection / Agent Split (and "Sim" → "Bot" rename)

**Status:** Draft
**Created:** 2026-06-05
**Branch:** `015-connection-agent-split`
**Input:** Split the single `Bot` concept into two layers — a **Connection** (your AI login: provider + key + runner, set up once) and an **Agent** (a competitor that plays exactly one game, defined as model + strategy). Rename the deterministic preset opponents from "Sim" to "Bot". Pre-launch refactor: no existing players to preserve.

---

## Summary

Today one `Bot` row does two unrelated jobs at once: it is **the AI login/infrastructure** (provider, key, the runner process) *and* it is **the competitor** (the thing that joins matches and earns a leaderboard standing). Cramming both into one object is why creating an agent is confusing, why the connect step gets buried, and why "who is this on the leaderboard" is ambiguous once there is more than one game.

This feature separates the two:

- A **Connection** is your AI login. You set it up once. It is game-agnostic. It carries the provider, the connection key, and the runner.
- An **Agent** is a single competitor in a single game. It is **(name + game + model + strategy)**. It is what appears on the leaderboard. One Connection can power many Agents — which is what lets you run Haiku, Sonnet, and Opus as three separate competitors on one Claude login and watch them fight.

At the same time, the freed-up word "bot" is repurposed: the built-in, deterministic, no-AI opponents (currently called "Sims") become **Bots** — structurally just Agents with no Connection.

Because the product has not launched, there is **no match history or players to migrate**. The schema is reshaped and recreated, not back-filled.

```text
USER
 └── Connection "My Claude"   ← provider + key + runner. Connect once. (/me/connections)
        ├── Agent "Haiku-HHH"    game: hoard-hurt-help · model: Haiku  · strategy A ─┐
        ├── Agent "Sonnet-HHH"   game: hoard-hurt-help · model: Sonnet · strategy A ─┼─► same HHH board
        └── Agent "Opus-HHH"     game: hoard-hurt-help · model: Opus   · strategy A ─┘
 (a Bot is an Agent with no Connection — kind = bot — runs deterministically in-loop)
```

---

## Goals

- Make "connect my AI" and "enter a competitor in a game" two distinct, clearly-modeled actions.
- Let one login (Connection) field many competitors (Agents), so model-vs-model and strategy-vs-strategy benchmarking needs no re-connecting.
- Make leaderboard identity unambiguous: one row = one Agent = one (model + strategy) in one game.
- Keep the first-time experience as a single smooth flow even though there are now two layers underneath.
- Remove the word "bot" as the name for a *user's* AI player everywhere; reserve "bot" for built-in scripted opponents.
- Remove the only piece of connect code that hardcodes Hoard-Hurt-Help's rules (the MCP-direct path).

## Non-Goals

- **No data migration / backfill.** Pre-launch; the schema is recreated, not migrated with preservation.
- **No second game built here.** Liar's Dice (spec 014) is out of scope; this feature only ensures the model does not bake in Hoard-Hurt-Help.
- **No agent-level "playbook that seeds a per-match copy."** Strategy simply *is* an agent property; there is no separate seeding concept.
- **No "clone agent to try a variant" feature** (an obvious later nicety; not in this cut).
- **No keeping the "Advanced: play directly over MCP (no runner)" path.** It is removed, not reworked.
- **No merge/push as part of spec/plan/tasks work.**

---

## Terminology (authoritative for this feature)

| Term | Meaning |
|---|---|
| **Connection** | A user's AI login: provider + connection key + runner process. Game-agnostic. Lives at `/me/connections`. |
| **Agent** | A competitor that plays exactly one game. Defined by **name + game + model + strategy**. The leaderboard entity. Lives at `/me/agents`. |
| **AI agent** | An Agent with `kind = ai` — powered by a Connection + a model. The thing a user builds. |
| **Bot** | An Agent with `kind = bot` — a built-in, deterministic, scripted opponent with **no Connection**. Formerly "Sim". |
| **Player** | An Agent's participation in one specific match (the per-match record: seat, scores). |

**Hard rule:** never call a *user's* AI player a "bot" — that is an **agent**. "Bot" is reserved for the scripted house opponents.

---

## User Scenarios & Testing

### User Story 1 — First-time: create an agent in one smooth flow (Priority: P1)

As a new user with no connection and no agent, I want to create my first competitor without having to understand "connections" vs "agents" up front, so that I can get playing quickly.

**Why this priority:** This is the on-ramp. If a first-timer can't get a working agent, nothing else matters.

**Independent Test:** Sign in as a brand-new user, go to `/me/agents`, choose "New agent." Because there is no connection yet, the flow transparently collects a provider, shows the connect message, waits for the runner to connect, and lets me name the agent and pick its model. End state: one Connection and one playing-ready Agent exist.

**Acceptance Scenarios:**

1. **Given** a signed-in user with zero connections and zero agents, **When** they start "New agent," **Then** the flow asks for a provider and shows a runner setup message (it does *not* dead-end asking them to "create a connection first").
2. **Given** the user has pasted the setup message and the runner has connected, **When** the connection goes live, **Then** the page advances to naming the agent and choosing its model (constrained to the connection's provider), with the game defaulting to the only available game.
3. **Given** the agent is named and saved, **When** the user lands on the agent's page, **Then** it shows the agent as ready and offers "find a match to join."
4. **Given** a user who already has a connection, **When** they start "New agent," **Then** the flow skips provider/connect and goes straight to pick-connection + name + model + strategy.

---

### User Story 2 — One connection, many agents (Priority: P1)

As a user who already connected my AI, I want to create additional agents on the same connection without connecting again, so that re-pasting the setup is never repeated.

**Why this priority:** This is the core payoff of the split; without it the two layers add cost and return nothing.

**Independent Test:** With one live connection, create a second agent on it. No setup message or re-connect is required. The runner already running serves the new agent's turns.

**Acceptance Scenarios:**

1. **Given** a live connection with one agent, **When** the user creates a second agent on that connection, **Then** no new key is issued and no re-connect step appears.
2. **Given** two agents on one connection, **When** both are in active matches, **Then** the single running runner plays turns for both.
3. **Given** a connection is paused, **When** turns come due for any of its agents, **Then** none of them play until the connection is resumed.

---

### User Story 3 — Benchmark several models on one login (Priority: P1)

As a user, I want to run the same game with the same strategy on different models as separate competitors, so that I can see which model plays better.

**Why this priority:** This is the headline reason the model carries on the agent. It is the product's "benchmarks measure your agent" promise made real.

**Independent Test:** On one Claude connection, create three agents for Hoard-Hurt-Help — one Haiku, one Sonnet, one Opus — and enter them in matches. Each appears as its own leaderboard row labeled with its model; the runner drives each with the correct model.

**Acceptance Scenarios:**

1. **Given** one connection, **When** the user creates agents pinned to different models of that provider, **Then** each agent stores its own model and the runner uses each agent's model for that agent's turns.
2. **Given** three model-variant agents that have played rated matches, **When** the leaderboard renders, **Then** there are three distinct rows, each showing the model it ran.
3. **Given** an agent on a Claude connection, **When** the user picks a model, **Then** only Claude models are offered (model choice is constrained by the connection's provider).

---

### User Story 4 — Bots are connectionless agents (Priority: P1)

As the platform, I need built-in scripted opponents ("Bots", formerly "Sims") so that matches can be filled and there is a baseline on the leaderboard — without any AI login.

**Why this priority:** Matches need opponents. Bots are how games fill and how baselines appear; the system is not usable for play without them.

**Independent Test:** Confirm a Bot exists with no connection, joins a match, plays deterministically, and appears on the leaderboard badged as a Bot — and never appears anywhere under `/me/connections`.

**Acceptance Scenarios:**

1. **Given** a Bot, **When** its record is inspected, **Then** `kind = bot` and it has no connection link.
2. **Given** a match needing fill, **When** Bots are seated, **Then** they take turns deterministically with no runner and no key.
3. **Given** the leaderboard, **When** it renders, **Then** Bots are clearly labeled as Bots and are separable from AI agents (e.g. an "agents / bots / both" view).
4. **Given** any page under `/me/connections`, **When** it lists connections, **Then** no Bot appears there.

---

### User Story 5 — An agent is a versioned (model + strategy) (Priority: P2)

As a user, I want each change to my agent's model or strategy to become a new **version** with its own rating, so that I can iterate while every leaderboard rank still means "this exact (model + strategy) scored X."

**Why this priority:** Important for benchmark integrity and a coherent model; the system can run on a single default version before iteration is polished.

**Independent Test:** Create an agent (version 1 = model + strategy); enter it in a match. Edit the strategy after it has played; confirm a version 2 is created, new matches use v2, and the completed match still shows v1. Each rated version has its own rank.

**Acceptance Scenarios:**

1. **Given** an agent whose current version is its (model + strategy), **When** it joins a match, **Then** it plays that version without the user re-typing anything.
2. **Given** the agent's current version has **not** yet played a rated match, **When** the user edits its model or strategy, **Then** the edit updates that same (still-draft) version — no version spam during setup.
3. **Given** the current version **has** played a rated match (it is frozen), **When** the user edits model or strategy, **Then** a new version (N+1) is created, becomes current, and is used by future matches.
4. **Given** a completed match, **When** the user later changes the agent, **Then** that match still reflects the exact version it ran (the version is the snapshot; history is never rewritten).
5. **Given** an agent with several rated versions, **When** the leaderboard renders, **Then** the public board shows one row per agent at its latest rated version, and the agent's page lists every version and its rank.

---

### User Story 6 — Manage a connection on its own page (Priority: P2)

As a user, I want a place to manage the login itself — see runner status, reissue or revoke the key, pause, or delete it — separate from my competitors.

**Why this priority:** Needed for real operation (keys leak, runners die), but distinct from first-play.

**Independent Test:** From `/me/connections`, open a connection, reissue its key, and confirm the agents it powers keep working until the new key connects; pausing the connection stops all its agents.

**Acceptance Scenarios:**

1. **Given** a connection, **When** the user reissues its key, **Then** a fresh setup message is shown once and the old key keeps working until the new one connects.
2. **Given** a connection with agents in matches, **When** the user deletes the connection, **Then** they are warned about the agents it powers and the action follows a clear rule (see Edge Cases).
3. **Given** a connection page, **When** it renders, **Then** it shows live runner/health status and lists the agents that run on it.

---

### User Story 7 — Leaderboard identity reads cleanly (Priority: P2)

As a spectator, I want each leaderboard row to clearly be one competitor — its name, its model, and (when relevant) its game — so that rankings are unambiguous.

**Why this priority:** The split's clarity payoff; the leaderboard already groups per game, so this is refinement rather than new capability.

**Independent Test:** Render the leaderboard with AI agents and Bots present; confirm each row is one Agent, labeled with model (for AI agents) and a Bot badge (for bots), within its game section.

**Acceptance Scenarios:**

1. **Given** a game section, **When** it renders, **Then** each row corresponds to exactly one Agent.
2. **Given** an AI agent row, **When** it renders, **Then** it shows the agent's name and its model.
3. **Given** the in-match display, **When** an agent plays, **Then** its shown name derives from the agent's name (not an unrelated random string).

---

### User Story 8 — "Bot" is gone as the word for a user's player (Priority: P3)

As Chris, I want the codebase and copy to stop calling a user's AI player a "bot," so that the terminology matches the product.

**Why this priority:** Hygiene and consistency; does not block function.

**Independent Test:** Grep the codebase and visible copy: there is no `Bot` model class and no user-facing text that calls a user's AI player a "bot"; "bot" appears only as the scripted-opponent kind/label.

**Acceptance Scenarios:**

1. **Given** the model layer, **When** inspected, **Then** there is no `Bot` model class; the concepts are `Connection` and `Agent`.
2. **Given** user-facing copy, **When** reviewed, **Then** a user's AI player is never called a "bot"; "bot" labels only scripted opponents.
3. **Given** the route surface, **When** inspected, **Then** management lives under `/me/connections` and `/me/agents` (no `/me/bots`).

---

## UI & Runner Surface

Full text wireframes for every screen: **[wireframes.md](./wireframes.md)** (authoritative for layout + copy). Highlights:

- **Navigation** gains two entries — **Connections** and **Agents** — replacing the single "My agents". Preset **Bots** (scripted opponents, formerly "Sims") are a labelled group, never under Connections.
- **`/me/connections`** lists logins; each is shown as **provider + optional nickname**, with metadata **`● Live · PID <pid> · key …<hint>`** — the **PID shows only while the runner is live** (it's the process to kill; it changes on restart), and the key-hint is the stable fingerprint. A `pending` connection that never connects shows a "waiting to connect" state and is GC'd after 24h.
- **Connection detail** holds the **runner setup message** (see runner copy below); reissue/revoke/pause/delete. The MCP-direct "Advanced" section is **removed**.
- **`/me/agents`** lists competitors (name · game · model · current version · health). **"+ New agent"** is the combined flow (folds in connection-create when there is none).
- **Agent detail** is state-driven (one next action) and adds a **Versions panel**: each version numbered + timestamped, with its model, strategy, and per-version rank; old versions retained.
- **Leaderboard** rows are agents at their latest rated version (model shown); the "Sims" filter becomes **"Bots"**.
- **Game viewer** shows in-match identity as **`handle/agent-name`** (+ model), replacing the old per-match "Alice_42" labels.

### Runner download + setup prompt (the rename's user-visible effect)

- The download is unchanged in form — one Python file via `curl … /runners/agentludum_agent.py`.
- The **key changes to `sk_conn_<hex>`** (a connection key) and the runner sends header **`X-Connection-Key`**.
- The pasted prompt reframes from per-bot to **per-connection** ("keep this running so it plays **all my agents'** games"), one session per match, only thinking on a turn.
- The second, **MCP-direct paste path is deleted** — the runner is the only connect method.

These UI requirements are normative:
- **FR-025**: A connection MUST be displayed as provider (+ optional user nickname) with metadata `● Live · PID <pid> · key …<hint>`; the **PID is shown only while the runner is live**; the key-hint is always shown as the stable identifier.
- **FR-026**: The connection setup message MUST use the `sk_conn_` key, the `X-Connection-Key` header, and per-connection wording ("plays all my agents' games"). The MCP-direct connect copy MUST NOT appear.
- **FR-027**: The agent detail page MUST show a Versions panel — each version's number, creation timestamp, model, strategy, and rank — with older versions retained and viewable.

## Edge Cases

- **Delete a connection that still powers agents** → block with a clear message, or require the agents be deleted/detached first (decision: block-and-explain; the user must remove its agents first). AI agents cannot be left "powered by nothing."
- **Provider change on a connection** → the connection's provider is effectively fixed by the login; changing it would invalidate every agent's model. Treat provider as set at connect time; to use another provider, make another connection. (No in-place provider switch in this cut.)
- **Agent's model not valid for the connection's provider** → reject at save; only offer models for the connection's provider.
- **Same connection, two agents in the same single match** → allowed; the play API disambiguates by `(agent_id, match_id)` + the agent-scoped token (FR-021), so moves never cross. NOT collapsed by `match_id`.
- **Bot with a connection / AI agent without one** → invalid; enforce `kind = bot ⇒ no connection`, `kind = ai ⇒ has connection`.
- **Connection that never connects** → created as `pending` on provider-select; resumable if abandoned; GC'd after 24h (FR-024).
- **Runner connects for a connection that has zero agents yet** → goes `active` and waits; no turns to play.
- **Key reissue while the runner is mid-match** → old key works until the new one first connects.
- **Editing a frozen version vs a draft version** → draft (unplayed) edits in place; frozen (played) edit forks a new version (FR-011).
- **`max_concurrent_games` reached** → join blocked with a clear message (FR-022).
- **Two users with the same agent name** → fine; `seat_name` = `handle/name` keeps them distinct in a match (FR-013).
- **Empty states** → no connections yet (combined flow handles it); a connection with no agents; an agent with no rated version yet (not on the board).

---

## Requirements

### Functional Requirements

- **FR-001**: The system MUST model a **Connection** (per-user AI login) carrying provider, a connection key, key lifecycle/lookup fields, runner/health fields, and pause/active status. It MUST NOT carry a model.
- **FR-002**: The system MUST model an **Agent** carrying name, game, model, strategy, a `kind` of `ai` or `bot`, and an optional link to a Connection. Supports US1–US7.
- **FR-003**: An Agent with `kind = ai` MUST have a Connection; an Agent with `kind = bot` MUST NOT have a Connection. The model MUST enforce this invariant.
- **FR-004**: The system MUST authenticate the runner/agent traffic by a **connection key** (header `X-Connection-Key`, key prefix `sk_conn_`), resolving to a Connection. Supports US2, US3.
- **FR-005**: The "next turn" resolution MUST return the most urgent turn across **all agents on the authenticated connection**, and MUST identify which agent (and thus which model, strategy, and game) the turn is for. Supports US2, US3. *(This is the one high-care logic area.)*
- **FR-006**: A single running runner for one connection MUST be able to play turns for multiple agents, using each agent's own model. Supports US2, US3.
- **FR-007**: Model choice for an AI agent MUST be constrained to models valid for its connection's provider. Supports US3.
- **FR-008**: Creating an agent when the user has no connection MUST walk the user through creating a connection inline (provider → connect → name/model), as one flow. Supports US1.
- **FR-009**: Creating an agent when the user already has a connection MUST let them pick an existing connection without re-connecting. Supports US2.
- **FR-010**: An agent's playing definition MUST live in versioned **(model + strategy)** records. Joining a match MUST use the agent's current version without re-entry. Supports US5.
- **FR-011**: Editing an agent's model or strategy MUST update the current version **if it has not yet played a rated match**, and MUST otherwise create a new version (N+1) that becomes current. Editing MUST be blocked while a version is mid-match. Rating MUST be computed **per version**. Supports US5.
- **FR-012**: Each match MUST reference the exact **version** an agent ran, so all history/read paths (viewer, exports, admin, summaries) show that version's (model + strategy) and later edits never rewrite a completed match. Supports US5. *(The version is the snapshot — no separate snapshot field.)*
- **FR-013**: A **Player** MUST link to an Agent via a real `agent_id` FK and to the **AgentVersion** that played via `agent_version_id`. The public in-match identity MUST be a `seat_name` derived as `"{handle}/{agent.name}"` (uniquified within the match); the integer `agent_id` MUST never be exposed as a public label. Every protocol/viewer field that previously exposed the string `agent_id` MUST use `seat_name`. Supports US7.
- **FR-021**: The play API MUST resolve the acting player by **(agent, match)**, not by match alone: `next-turn` MUST key candidates by `(agent_id, match_id)` and return an **agent-scoped token** that write endpoints (`submit`/`message`/`leave`) require, so a connection fielding two agents in one match can never have a move applied to the wrong player. Supports US2, US3. *(Closes the routing hole behind the past freeze.)*
- **FR-022**: Joining a match MUST be blocked when the agent's connection already powers `max_concurrent_games` active matches, with a clear message. Supports US2.
- **FR-023**: Valid models per provider MUST come from a single canonical config (`PROVIDER_MODELS`); setting a model not valid for the connection's provider MUST be rejected. Supports US3 (FR-007).
- **FR-024**: Connection **health** (live / stalled / ready) MUST be computed at the connection level across its agents (heartbeat, `stall_threshold`, `max_concurrent_games`, paused state) — not via single-agent assumptions. The combined-create flow MUST handle a connection that never connects: a `pending` connection is created on provider-select, can be resumed if abandoned, and is garbage-collected after 24h. Supports US1, US6.
- **FR-014**: `/me/connections` and `/me/agents` MUST exist as separate pages with separate top-level navigation entries. Bots MUST NOT appear under `/me/connections`. Supports US4, US6, US8.
- **FR-015**: Connection management MUST support reissue (graceful overlap — old key valid until new one connects), revoke (immediate cutoff), pause/resume, and delete. Deleting a connection that still powers agents MUST be blocked with a clear message. Supports US6.
- **FR-016**: The leaderboard MUST treat one row as one Agent, label AI agents with their model, and clearly distinguish Bots from AI agents, within each game's section. Supports US7.
- **FR-017**: The "Advanced: play directly over MCP (no runner)" connect path MUST be removed entirely. Supports the rules-decoupling goal.
- **FR-018**: There MUST be no `Bot` model class; "bot" MUST survive only as the `AgentKind` value and as a UI label for scripted opponents. No user-facing copy may call a user's AI player a "bot." Supports US8.
- **FR-019**: Because there are no players to preserve, the schema MUST be reshaped and recreated (no preserving migration/backfill is required), and the dev test database MUST build cleanly from the models.
- **FR-020**: The auth header rename and key-prefix change MUST be reflected in the runner setup message and the runner itself.

### Key Entities

- **Connection** — `id`, `user_id`, `provider` (`ConnectionProvider`), key lookup + previous-key lookup + key hint, status (`pending`/`active`/`paused`) + pause fields, first-connected / last-seen / runner-pid, `max_concurrent_games`, `stall_threshold`, timestamps. No model field.
- **Agent** — identity only: `id`, `user_id`, optional `connection_id` (null for bots), `kind` (`AgentKind`: ai/bot), `name`, `game` (slug), `current_version_id`, archived/status, and the deterministic-bot config (formerly `sim_*`) when `kind = bot`. The competitor identity; its playing definition lives in versions.
- **AgentVersion** — `id`, `agent_id`, `version_no`, `model`, `strategy_text`, `frozen_at` (set when it first plays a rated match → immutable). The rated unit. Bots have one implicit version (their config).
- **Player** — `id`, `match_id`, `user_id`, `agent_id` (FK → agents), `agent_version_id` (FK → the version that played), `seat_name` (public display = `handle/name`, uniquified per match), per-match scores, joined/left timestamps.

---

## Success Criteria

- **SC-001**: A brand-new user can go from "no connection, no agent" to a connected, match-ready agent in a single uninterrupted flow (no dead-end that says "make a connection first").
- **SC-002**: A user can add a second (and third) agent to an existing connection with **zero** re-connect / re-paste steps.
- **SC-003**: Three model-variant agents on one connection appear as three distinct leaderboard rows, each labeled with its model, and each is driven by its own model.
- **SC-004**: Every Bot has no connection and never appears under connection management, yet still plays matches and appears (labeled) on the leaderboard.
- **SC-005**: Editing an agent's strategy never changes a completed match's recorded strategy, and is impossible while that agent is in an active match.
- **SC-006**: No leaderboard row, match view, or management page calls a user's AI player a "bot"; "bot" appears only for scripted opponents.
- **SC-007**: The full preflight gate passes (`ruff`, `mypy app/ mcp_server/`, `pytest -q`) with the reshaped schema and updated tests, with no suppressions.
- **SC-008**: There is no `/me/bots` route and no `Bot` model class remaining.

---

## Assumptions

- **Pre-launch, no real data.** Confirmed by Chris: there are no players to preserve, so schema recreation (not a preserving migration) is acceptable, and a pre-launch prod DB may be reset.
- **One game today.** Only `hoard-hurt-help` is registered; the game picker is hidden/auto-set while a single game exists, but nothing bakes the slug in. (Liar's Dice, spec 014, is the eventual second game.)
- **Combined create flow** (US1) is the chosen on-ramp; strictly-separate creation was rejected.
- **Provider is fixed per connection** at connect time; switching providers means a new connection (no in-place provider change in this cut).
- **Provider on connection; (model + strategy) on a versioned agent** — an agent is an identity; each (model + strategy) it runs is a version with its own rating. Changing model or strategy makes a new version (once the current one has played), so a rank always names a fixed (model + strategy). *(Revised after review — resolves the earlier "different strategy = different agent" contradiction.)*
- **MCP-direct connect path is dropped**, not reworked.
- **"Bot" repurposed** from the old "Sim" concept; the standing "never say bot" rule is replaced by "agent = user's AI player; bot = scripted opponent."
- **Delete-connection rule:** block while it still powers agents (user removes agents first). Chosen over cascade-delete to avoid surprise loss of competitors/standings.

---

## Constitution Check (CLAUDE.md)

- **PASS — Async & types:** New/changed route handlers and DB calls remain `async`; all new function signatures carry type annotations. (Enforced by `mypy` in preflight, SC-007.)
- **PASS — No suppressions / no bare except:** Spec forbids suppressions to pass checks (SC-007) and the existing constitution rule stands.
- **PASS — Testing:** New game/engine-adjacent logic (turn resolution across a connection's agents; bot seating as connectionless agents) MUST get tests; test DB stays SQLite in-memory. (FR-005, FR-006; SC-007.)
- **PASS — File structure:** New responsibilities split by domain (connection vs agent routes/templates); no vague `utils.py`. Templates split `bots/` → `agents/` + `connections/`.
- **PASS — Delivery rules:** One feature per branch (`015-connection-agent-split`); no push/merge during spec/plan/tasks; preflight gate before any push.
- **NOTE — High-care area:** FR-005 (turn resolution) touches the path that previously caused a mid-deploy game freeze; the plan MUST treat it as the riskiest unit and cover it with focused tests.

**Result: PASS** (no constitutional conflicts; one high-care implementation area flagged for the plan).
```
