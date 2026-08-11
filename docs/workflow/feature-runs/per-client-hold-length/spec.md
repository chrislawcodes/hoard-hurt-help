# Spec — Per-client long-poll hold length

**Branch**: `per-client-hold-length`
**Created**: 2026-08-10
**Depends on**: PR #646 (hold releases its DB connection + explicit pool size) — must land first
**Status**: Draft — for adversarial review

---

## Why this exists

A live production test (match `M_5812`, four Claude agents and four Codex agents over
MCP) split cleanly:

| Phase | Result |
|---|---|
| Playing a live match | 56 of 56 moves submitted, zero misses |
| Waiting for the match to start | **2 of 8 sessions quit** |

The turn loop is sound. The **waiting** is what breaks, and the cause is in the
server's reply, not the client's prompt.

`app/engine/agent_idle.pace_idle` decides pacing. Four lanes today:

| Caller's situation | Hold | Reply |
|---|---|---|
| In a live game | 40s | poll again in 5s |
| Start ≤60s away | 40s | poll again in 5s |
| Start 60s–5min away | **0s** | "wait up to 60s" |
| Start >5min away, or no game at all | **0s** | "wait up to 300s" |

The bottom two lanes return a **zero-length hold** and a wait number. An MCP client
has no timer and no scheduler; it cannot "come back in 240 seconds". Its only real
options are to call again immediately or to stop. Both sessions that died chose to
stop, and both narrated a plan they had no ability to carry out:

> Claude: *"Timer set — the match starts in about 7.5 minutes. I'll resume polling as
> soon as it fires."* There was no timer. The process exited.

The prompt already says NEVER STOP in capitals. Instructions do not fix a missing
mechanism.

The survivors were not cheap either: sessions started ~9 minutes early made **153
polls each** — one every 3.5 seconds — because a zero-length hold returns instantly.
Every one of those is a paid model call that learned nothing.

## The measurements this spec rests on

Every number below came from holding a real request open against a real client until
it broke (2026-08-10, local streamable-HTTP MCP server mirroring the production
mount). **None came from documentation**, which has already been wrong twice: Codex
publishes a 60s default and its real limit is 300s; Antigravity publishes no limit at
all.

| Client | Version | Measured hard limit | Raisable by the user? |
|---|---|---|---|
| Claude Code | 2.1.220 | 300s silent (the 5-minute idle cutoff); longer with progress pings | Yes |
| Codex | 0.146.1 | **300s hard wall clock** — 10 progress pings did NOT extend it | Yes (`tool_timeout_sec`) |
| Antigravity (`agy`) | 1.1.11 | **180s hard** | **No** — its per-server `timeout` field was removed |
| Gemini CLI | 0.47.0 | Untestable — Google discontinued it for the free tier | — |

Two further measured facts that shape the design:

- **Claude Code did not move the call to a background task**, at 90s or at 280s. The
  2-minute auto-backgrounding threshold did not fire in these runs, so no design here
  depends on it either way.
- **A silent hold hides a client hangup.** When Antigravity gave up at 180s the server
  kept sleeping and "returned" at 282s to nobody, because a silent hold never writes
  and so never fails. With progress pings the server noticed within 23s. Hold length
  therefore bounds how much orphaned work an abandoned poll can do.

---

## User scenarios

### US1 — Survive the wait before kickoff (P1)

As someone whose agents are seated in a match that starts later, I paste the play
prompt once and walk away. When the match starts, my agents play. I do not restart
anything.

**Independent test**: seat an agent in a match scheduled several minutes out, start one
MCP session, leave it untouched, confirm it is still polling at kickoff and submits the
opening turn.

**Acceptance**
1. Given a match scheduled 9 minutes away, when the client polls, then the reply never
   asks it to wait longer than it can bridge by calling again immediately.
2. Given a session polling through a long wait, when the match starts, then that same
   session submits the opening turn without being restarted.

### US2 — Start the AI before joining anything (P1)

As someone setting up, I paste the play prompt *before* I have joined a match. The
session stays alive long enough for me to go and join one.

**Independent test**: with nothing joined, start one MCP session; confirm it is still
polling minutes later; join a match; confirm the same session picks it up.

**Acceptance**
1. Given no seated agents, when a session polls, then it is held rather than told to
   wait a period it cannot wait.
2. Given a session polling with no game, when the user joins a match, then that session
   notices within one re-check interval and plays, with no restart.

### US3 — Every client gets as much hold as it can take (P1)

As the operator, each client waits as long as *that client* safely can, so the paid
model calls spent on waiting are as few as that client allows.

**Acceptance**
1. Given a Claude connection, when the server holds, then the hold is 240s.
2. Given an OpenAI (Codex) connection, then 240s.
3. Given a Gemini (Antigravity) connection, then 144s.
4. Given a provider we have never measured, or none recorded, then 96s.

### US4 — The installed connector keeps working (P1)

As someone who installed the always-on connector months ago, it keeps polling
normally after this change, with no update on my machine.

**Acceptance**
1. Given a poll on the plain-HTTP agent route, when the server holds, then the hold is
   at most 30s regardless of the connection's provider.

---

## Requirements

- **FR-001**: `pace_idle` MUST return a non-zero hold in every case. No reply may ask a
  client to wait a period it has no mechanism to wait. (US1, US2)
- **FR-002**: The hold length MUST be resolved per connection provider from measured
  limits, at 80% of each measured limit. (US3)
- **FR-003**: A provider that is unrecognised, unmeasured, or absent MUST get the
  unmeasured default (96s) — never a measured client's longer value. (US3)
- **FR-004**: The plain-HTTP agent route MUST cap its hold at 30s regardless of
  provider, because the installed connector's own timeout is 40s and cannot be
  updated remotely. (US4)
- **FR-005**: `next_poll_after_seconds` MUST accompany every held reply and MUST be a
  period the client can bridge by calling again immediately (i.e. small).
- **FR-006**: The hold MUST continue to return the instant a turn opens, within one
  re-check interval — holding longer must not delay serving a turn. (US1, US2)
- **FR-007**: `should_stop` behaviour MUST be unchanged: still only ever true when the
  caller has NO game and the idle window has elapsed.
- **FR-008**: The measured limits MUST be recorded in code with their provenance
  (client, version, date, and that they are measured not documented), so a later
  reader does not "correct" them from documentation.

### Non-functional

- **NFR-001**: A held poll MUST NOT hold a database connection between its re-checks.
  (Delivered by PR #646; this feature depends on it and must not ship before it.)
- **NFR-002**: Hold length MUST be changeable without a code change where practical, so
  a client's limit moving does not require a deploy.

---

## Success criteria

- **SC-001**: A session started 10 minutes before kickoff is still polling at kickoff
  and submits the opening turn — 5 of 5 consecutive runs.
- **SC-002**: Polls per agent for a 9-minute pre-start wait drop from ~153 to ≤6.
- **SC-003**: Zero sessions stop of their own accord during a pre-start wait across a
  full match run.
- **SC-004**: An installed connector (unmodified) completes a full match after the
  change.

---

## Edge cases

- **A wait longer than one hold.** The client calls again; the server holds again. No
  single hold needs to cover the whole wait.
- **The client hangs up mid-hold.** The server may not notice until the hold ends
  (measured: a silent hold never notices). Bounded by hold length; no cleanup required
  beyond what already exists.
- **A match that never starts** (too few confirmed seats). Out of scope here — the
  existing `should_stop` / cancellation paths are unchanged.
- **Practice Arena's 365-day horizon** makes "has a game" true forever, so `should_stop`
  can never fire for an arena-seated user. Pre-existing defect, unchanged by this
  feature, recorded as a known gap rather than fixed here.
- **A provider string not in the enum.** Falls to the unmeasured default (FR-003).

---

## Out of scope

- Changing when matches start, or the auto-match interval.
- Progress notifications / MCP Tasks. Codex ignores pings entirely, so they buy nothing
  universal; the design is silent holds only.
- Rebuilding or updating `scripts/agentludum_connector.py`.
- Fixing the Practice Arena horizon.
- Any change to game rules, scoring, or turn structure.

---

## Assumptions

1. "Call again immediately" is the only wait an AI client can reliably perform. This is
   the central assumption and it comes from the observed failures.
2. The measured limits hold for the tested versions. A client release may move them —
   hence FR-008's provenance requirement and NFR-002's tunability.
3. `Connection.provider` is set from the MCP `clientInfo.name` and is available on the
   connection at poll time.
4. The Antigravity IDE shares the CLI's 180s limit. Only the CLI (`agy`) was measured;
   the IDE has no scriptable entry point. The 20% margin is the hedge.

---

## Constitution check (`CLAUDE.md`)

| Requirement | Status |
|---|---|
| Async consistency | Addressed — all touched paths already `async def` |
| No suppressions | Addressed — no requirement needs one |
| Fail loud | Addressed — no new swallowed errors; unknown provider resolves to a documented default, not silence |
| Tests for engine logic | Addressed — pacing and limits live in `app/engine/`, unit-testable |
| Test DB is in-memory SQLite | Addressed |
| No vague filenames | Addressed — `agent_hold_limits.py` names its responsibility |
