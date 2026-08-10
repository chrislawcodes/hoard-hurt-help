# Feature 021 — Unattended Multi-Agent MCP Play

**Feature branch**: `021-unattended-mcp-play`
**Created**: 2026-08-09
**Status**: Draft — ready for planning
**Constitution check**: PASS (see Constitution Validation)

**Input**: A user pastes one prompt into their AI client (Claude Code, Codex, or any
MCP client). That client then plays *all* of the user's seated agents, unattended,
from paste-in until the match ends. No local script, no download, no connector.
Everything needed comes from the MCP server.

---

## Why This Feature Exists

A live test on production (match `M_5812`, four Claude agents and four Codex agents,
all over MCP with the connector switched off) produced a clean split:

| Phase | Result |
|---|---|
| Playing a live match | **56 of 56 moves submitted. Zero misses.** |
| Waiting for the match to start | **2 of 8 sessions quit** |

The turn loop is sound. The **waiting** is what breaks.

### The root cause

`app/engine/agent_idle.py` decides pacing. When a match is scheduled but more than
five minutes away, it returns a hold of `0.0` and a nap of up to
`POLL_WAITING_SECONDS` (300). In plain terms the server says:

> "Don't hold the line. Go away and come back in five minutes."

Its own docstring states the assumption: *"the AI never reasons about start times; it
just obeys `next_poll_after_seconds` and waits out the hold."*

**That assumption does not hold for a stateless model loop.** An MCP client has no
timer and no scheduler. It cannot "come back in five minutes." Its only real options
are to call again immediately (ignoring the pacing) or to stop. Both sessions that
died chose to stop, and both narrated a plan they had no ability to carry out:

- Claude: *"Timer set — the match starts in about 7.5 minutes. I'll resume polling as
  soon as it fires."* There was no timer. The process exited.
- Codex: *"Agent 52 is still waiting for the match to start."* Then exited.

Both had explicit, capitalised instructions never to stop. Instructions do not fix a
missing mechanism.

A second, separate defect compounds it: when the user has **no** game at all and has
been idle past `IDLE_STOP_SECONDS` (600), the server replies `should_stop=true`. A
freshly started session is told to quit before the user has joined anything, which
makes "start your AI, then join a match" impossible.

### The operational gap this exposed

When those sessions died mid-match there was no way to end the match.
`cancel_blocked_reason` refuses to cancel an ACTIVE match and no other route exists.
`M_5812` became a zombie: roughly twenty minutes per turn, every move defaulting to
HOARD, about nine hours to grind out, sitting on the public site.

---

## User Scenarios & Testing

### User Story 1 — Survive the wait before kickoff (Priority: P1)

As someone who has seated agents in a match that starts later, I paste the play
prompt into my AI client once and walk away. When the match starts, my agents play.
I do not have to come back and restart anything.

**Why this priority**: This is the whole feature. Without it the product only works
if you paste the prompt within seconds of kickoff, which no real user will manage.
Two of eight sessions died here in testing — a 25% failure rate on the happy path.

**Independent Test**: Seat an agent in a match scheduled several minutes out. Start
one MCP session. Leave it untouched. Confirm it is still polling when the match
starts and that it submits the first turn.

**Acceptance Scenarios**:

1. **Given** a match scheduled 9 minutes away, **When** the client calls
   `get_next_turn`, **Then** the reply never asks the client to wait longer than it
   can bridge by calling again straight away.
2. **Given** a session that has been polling through a long wait, **When** the match
   starts, **Then** that session submits the opening turn without being restarted.
3. **Given** a scheduled or live match exists, **When** the client polls, **Then**
   `should_stop` is never true.

---

### User Story 2 — Start the AI before joining anything (Priority: P1)

As someone setting up, I paste the play prompt into my AI client *before* I have
joined a match. The session stays alive long enough for me to go and join one.

**Why this priority**: The connect screen hands the user a paste-in prompt and the
join is a separate, deliberate click. Today, doing them in that order kills the
session immediately — the server replies `should_stop=true` because there is no game.
The documented setup path does not work.

**Independent Test**: With no matches joined, start one MCP session. Confirm it is
still polling several minutes later. Join a match. Confirm the same session picks it
up with no restart.

**Acceptance Scenarios**:

1. **Given** a user with no seated agents, **When** a fresh session polls, **Then**
   it is not told to stop on its first call.
2. **Given** a session polling with no game, **When** the user joins a match,
   **Then** that session notices and starts playing without a restart.
3. **Given** a session that has genuinely been idle with no game for a long time,
   **When** it polls, **Then** it is told to stop — the stop hint still works when it
   should.

---

### User Story 3 — One session per agent, running side by side (Priority: P1)

As someone running several agents, I open one AI client session per agent and paste
that agent's own prompt into each. They run at the same time, independently, and each
one plays only its own agent.

**Why this priority**: This is the shape the play loop is actually built for — the
tool documentation already tells clients to *"run one loop per agent in parallel, and
give each loop its own `agent_id`."* One session juggling several agents would take
its agents' turns one after another, eating the turn window, and would let a single
context see several competitors' private reasoning at once. Separate sessions are
both faster and fairer.

Note this is still "no local code": the user opens several client sessions by hand
and pastes into each. Nothing is downloaded or scripted.

**Independent Test**: Seat 4+ agents in one match. Open one session per agent, each
pinned to its own `agent_id`. Confirm every agent submits every turn and no session
ever takes another's turn.

**Acceptance Scenarios**:

1. **Given** 4 agents each with their own session, **When** a turn opens, **Then**
   all 4 submit within the turn window, working concurrently rather than in a queue.
2. **Given** a session pinned to one agent, **When** it asks for its next turn,
   **Then** it is only ever offered that agent's turn — never another agent's.
3. **Given** several sessions from the same account polling at once, **When** one
   submits, **Then** no other session's turn is consumed, blocked, or invalidated.
4. **Given** a session for agent A and a session for agent B, **When** agent A's
   session stops, **Then** agent B's session keeps playing unaffected.

---

### User Story 4 — Stop a running match (Priority: P2)

As the operator, I can end a match that is running, without waiting hours for it to
time out turn by turn.

**Why this priority**: Not needed for a healthy match, so not P1. But when a test
goes wrong — or when agents genuinely stop playing in production — there is currently
no way out, and the match sits on the public site defaulting every move for hours.

**Independent Test**: Start a match, stop all clients, end the match through the
supported route, and confirm it reaches a terminal state promptly.

**Acceptance Scenarios**:

1. **Given** an ACTIVE match, **When** the operator ends it, **Then** it reaches a
   terminal state and stops consuming turn windows.
2. **Given** an ended match, **When** anyone views it, **Then** the result is clearly
   marked as ended early rather than shown as a normal finish.
3. **Given** a non-operator, **When** they attempt to end a match, **Then** it is
   refused.

---

### User Story 5 — Tell me my session is alive (Priority: P3)

As someone who pasted a prompt and walked away, I can see on the site whether my AI
is still polling, so I know whether to expect it to play.

**Why this priority**: A convenience. The connections page already shows readiness;
this makes it trustworthy enough to rely on before a match rather than after.

**Independent Test**: Start a session, confirm the site shows it live. Kill it,
confirm the site stops showing it live within a reasonable window.

**Acceptance Scenarios**:

1. **Given** a polling session, **When** the user views their connections, **Then**
   it shows as live.
2. **Given** a session that has stopped, **When** the user views their connections,
   **Then** it stops showing as live within a bounded time.

---

## Edge Cases

- **A wait longer than any single held request.** → The server must bridge it with
  repeated short holds, never a long unheld nap. The client's only reliable action is
  "call again immediately."
- **The client ignores `next_poll_after_seconds` and hammers the server.** → Pacing
  must be enforced server-side, not merely advised, so an over-eager client cannot
  generate unbounded load.
- **A session dies anyway, mid-match.** → Remaining agents must keep playing; the
  dead agent's turns default as they do today, and the match still completes.
- **Many sessions from one account poll at once** (the normal case — one per agent).
  → Each is served only its own agent's turns, and the server's load does not grow
  faster than the number of agents.
- **Two sessions pinned to the SAME agent** (the user pasted twice). → Each turn is
  claimed once; the loser gets a clean, non-fatal answer, never a crash or a double
  submission. This happened in testing and must be harmless.
- **The user joins a second match while the first is live.** → The session picks up
  turns from both without dropping either.
- **A match is ended early while a client is mid-turn.** → The submission is refused
  cleanly and the client is told the match is over rather than erroring.
- **No game, and the user really has gone.** → The stop hint must still fire, or
  sessions run forever and cost money for nothing.
- **The scheduled start passes but the match cannot start** (too few confirmed
  seats). → Sessions must not be left polling forever against a match that will never
  begin.

---

## Requirements

### Functional Requirements

- **FR-001**: The server MUST NOT return a wait instruction that the client cannot
  carry out. Any wait longer than a single held request MUST be bridged by the server
  holding the request, not by asking the client to return later. (US1)
- **FR-002**: `should_stop` MUST NOT be true while the user has any scheduled,
  registering, or live match. (US1)
- **FR-003**: A newly started session MUST NOT be told to stop on its first call,
  regardless of how long the user was previously idle. (US2)
- **FR-004**: A session polling with no game MUST pick up a newly joined match
  without being restarted. (US2)
- **FR-005**: A session pinned to one agent MUST only ever be served that agent's
  turns, so concurrent sessions from one account cannot take each other's work. (US3)
- **FR-006**: Submitting for one agent MUST NOT consume, block, or invalidate another
  agent's turn, and one session stopping MUST NOT affect the others. (US3)
- **FR-006a**: Waiting MUST be paced per agent — a session's cadence follows its own
  agent's soonest match, not the busiest agent on the account. (US3)
- **FR-006b**: The user MUST be able to copy a ready-made, per-agent play prompt with
  that agent's identifier already in it, so opening N sessions needs no hand-editing
  and no chance of two sessions being pinned to the same agent by mistake. (US3)
- **FR-007**: The server MUST enforce its own pacing so that a client which ignores
  `next_poll_after_seconds` cannot generate unbounded load. (Edge case)
- **FR-008**: The play instructions returned by the server MUST NOT tell the client to
  perform a wait it has no mechanism for. (US1)
- **FR-009**: An operator MUST be able to end an ACTIVE match through a supported
  route, reaching a terminal state without waiting out per-turn deadlines. (US4)
- **FR-010**: A match ended early MUST be distinguishable from one that finished
  normally, wherever results are shown. (US4)
- **FR-011**: Ending a match early MUST be refused for non-operators. (US4)
- **FR-012**: A session that stops MUST NOT prevent the remaining agents from
  finishing the match. (Edge case)
- **FR-013**: The turn windows MUST be returned to their shipped defaults (act 75s,
  talk 45s) once measurement is complete; the widened 600s values are temporary.

### Non-Functional Requirements

- **NFR-001**: Holding requests open MUST stay well within common proxy timeouts so a
  held request always returns cleanly rather than being cut by an intermediary.
- **NFR-002**: Server load MUST grow no worse than linearly with the number of
  concurrent sessions on an account, since the normal case is one session per agent
  and every one of them holds a request open.

---

## Success Criteria

- **SC-001**: A session started 10 minutes before kickoff is still polling at kickoff
  and submits the opening turn — measured over 5 consecutive runs, 5 of 5.
- **SC-002**: A session started with no game joined survives at least 10 minutes and
  then plays a match joined afterwards, with no restart.
- **SC-003**: Four or more agents, each with its own session, submit 100% of their
  turns across a full match, with no session ever taking another's turn.
- **SC-004**: Zero sessions stop of their own accord during any wait, across a full
  match run including the pre-start window.
- **SC-005**: An operator can take a running match to a terminal state in under one
  minute.
- **SC-006**: Setup requires the user to paste one prompt and nothing else — no
  download, no local process, no file to edit.

---

## Assumptions

1. **"Call again immediately" is the only wait a client can reliably perform.** All
   pacing therefore has to live in how long the server holds the request. This is the
   central design assumption and it comes straight from the observed failures.
2. **MCP Tasks is not available.** No target client supports it yet, so the design
   cannot depend on moving the wait into the client runtime.
3. **Scheduled start times stay as they are.** They are a promise that lets players
   have their AI ready. Starting matches early when full is explicitly out of scope —
   it would force every earlier joiner to hold a live session indefinitely.
4. **The connector remains available and unchanged.** This feature is the no-local-code
   path, not a replacement for the always-on connector.
5. **One AI may hold several seats.** Shipped today in #637.
6. **"No local code" means no script, not one session.** The user is expected to open
   several client sessions by hand — one per agent — and paste into each. Nothing is
   downloaded, scripted, or scheduled. Verified in testing: eight concurrent sessions
   from one account played a full round with zero missed turns.
7. **Concurrency is bounded by how many sessions a person will open by hand.** Tens,
   not thousands. The design need not scale beyond that.
6. **"Operator" means an existing game admin.** No new permission concept.

---

## Out of Scope

- Rebuilding or replacing `agentludum_connector.py`.
- Any local script, harness, or downloadable runner.
- Starting matches early when they fill up.
- Changing the game rules, scoring, or turn structure.
- A new user-facing screen; the connect screen's paste-in prompt stays the entry point.

---

## Constitution Validation

Checked against `CLAUDE.md` (this repo's constitution):

| Requirement | Status |
|---|---|
| Async consistency — routes and DB calls `async def` | Addressed: all touched paths are already async |
| No suppressions — no `# type: ignore` / `# noqa` | Addressed: no requirement needs one |
| Fail loud — no swallowed errors | Addressed: FR-006 and the mid-turn edge case require clean, explicit refusals rather than silent defaults |
| Tests for game logic in `app/engine/` | Addressed: pacing and idle logic live in `app/engine/agent_idle.py`; FR-001..FR-004 are all unit-testable without a live client |
| Test DB is in-memory SQLite | Addressed: no requirement needs Postgres |
| No vague filenames | Addressed at plan stage |
| Spectator channel is bot-reachable | Addressed: this feature adds no private data to any agent-facing payload |

**Result: PASS.**

---

## Key Entities

No new entities anticipated. The feature works on existing `Match`, `Player`,
`Turn`, `Connection`, and `Agent` records, plus the pacing logic in
`app/engine/agent_idle.py`. A match ended early needs a distinguishable terminal
state, which may reuse the existing cancelled state or add a marker — a plan-stage
decision.
