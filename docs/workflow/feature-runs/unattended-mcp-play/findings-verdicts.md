# Spec review findings — 021 unattended-mcp-play

Two adversarial reviewers on `spec.md`: `feasibility-adversarial` (F) and
`requirements-adversarial` (R). Every finding gets a row. No finding is dropped
without a recorded reason.

**Outcome: STOP. Do not build this spec as written.** Four blockers invalidate the
core design direction (FR-001), and three of them need a product decision that a
single review round cannot settle. Per the Thin skill's own routing rule, that is
the signal this was never a Thin feature.

| # | Lens | Finding | Verdict | Reason |
|---|------|---------|---------|--------|
| F1 | feasibility | FR-001 is unbounded. Practice Arena is `REGISTERING` with `scheduled_start = now + 365 days`, so `has_game` is permanently true → ~1.26M consecutive held calls. MCP holds are **25s** (`_NEXT_TURN_HOLD_SECONDS`), not 40s. | **fix now — blocker** | Design-invalidating. FR-001 must be bounded before anything is built. |
| F2 | feasibility | Held requests exhaust the DB pool. `make_engine` sets no `pool_size`/`max_overflow` → 15 connections total, one replica. Each hold pins one and `_serve_one_turn` never rolls back between 5s ticks. Breaks in the teens; the 8-session test sat under the limit. | **fix now — blocker** | One session per agent is the design; the ceiling is below "tens". |
| F3 | feasibility | FR-003 unimplementable. MCP is mounted `stateless_http=True`; `token.client_id` is the Google subject and DCR client_id is per install. Neither changes per session. | **fix now — blocker** | Requirement must be reframed per *polling run* (per-connection), not per session. |
| F4 | feasibility | **FR-009's premise is factually wrong.** `POST /matches/{id}/cancel` (`require_platform_admin`) explicitly allows ACTIVE. `admin_web.py` delete also works. Only the *game-admin* screens consult `cancel_blocked_reason`. | **fix now** | Verified and acted on: M_5812 cancelled via that route. US4 shrinks to "make the game-admin path agree". |
| F5 | feasibility | FR-007 and FR-001 conflict: FR-001 makes "call again immediately" correct, so the server cannot tell a compliant client from an abusive one. | **fix now** | Rewrite FR-007 as concurrent-hold cap + single-flight, not "enforce pacing". |
| F6 | feasibility | Real obstacles to stopping an ACTIVE match: `ALLOWED[ACTIVE] = {COMPLETED}` forbids the transition; `_watchdog` (2s) restarts any ACTIVE match with no task, racing `cancel_match`'s stop-then-commit order; cooperative cancel may write one more submission. | **fix now** | Small, clearly-correct fix: commit first, then stop; add ACTIVE→CANCELLED to ALLOWED. |
| F7 | feasibility | FR-010 cannot reuse `CANCELLED` — that state already means "never started" (underfilled lobbies, stale arenas) and renders identically in lobby/my-matches. | **defer to re-spec** | Needs a product decision on whether an ended match counts toward ratings. |
| F8 | feasibility | FR-002 + permanently-`REGISTERING` Practice Arena means `should_stop` can never fire for any user who took an arena seat. | **fix now — blocker** | Contradicts the spec's own "user really has gone" edge case. |
| F9 | feasibility | FR-013 is an ops action, not code; defaults already ship 75/45. Also `per_turn_deadline_seconds` is stamped per match, so unsetting the env var leaves existing scheduled matches at 600. | **fix now** | Move to a deploy checklist; add the missing step for existing matches. |
| F10 | feasibility | US5 has no FR and no SC, and liveness is per-`Connection` so N sessions share one row — AS2 unsatisfiable. | **reject as specced** | Already out of PR scope; restate per agent if revived. |
| F11 | feasibility | FR-005/006/006a appear already shipped (`agent_id` threaded end to end). | **fix now** | Restate as regression guards, not new build. |
| R1 | requirements | FR-002 and FR-006a already true on `origin/main` — they test nothing. The real gap is unnamed: `pace_idle` returns `(0.0, 300)` when `seconds is None`. | **fix now** | Add the missing no-game requirement. |
| R2 | requirements | FR-002 contradicts the "match never starts" edge case — a stuck `REGISTERING` match satisfies FR-002 forever. | **fix now — blocker** | Needs FR-002a with a named horizon. |
| R3 | requirements | SC-006 ("one prompt and nothing else") contradicts US3/FR-006b (N sessions, N pastes). | **fix now** | Reword SC-006 to N pastes. |
| R4 | requirements | "Join a second match" edge case contradicts per-agent pinning; `get_next_turns` is not agent-scoped, quietly violating FR-005. | **fix now** | Say explicitly whether `get_next_turns` is in scope. |
| R5 | requirements | FR-001 / FR-007 / NFR-002 cannot all hold. NFR-002's "linear" misses the real risk — linear growth against a fixed pool *is* the outage. | **fix now — blocker** | Same root as F2/F5. Needs concrete numbers. |
| R6 | requirements | The cost tradeoff is absent. `agent_idle.py`'s stated goal is "ask as rarely as possible"; FR-001 reverses it with no budget. | **fix now — blocker** | Needs a decision on cost per waiting agent. |
| R7 | requirements | FR-010 vs Key Entities: a marker means a migration, so "no new entities" and the small-change claim are both wrong. | **defer to re-spec** | Same product decision as F7. |
| R8 | requirements | "Fresh session" undefined and opens an escape hatch: a reconnecting client could reset its idle clock forever. | **fix now** | Same as F3. |
| R9 | requirements | Untestable wording throughout: FR-001 "a wait the client cannot carry out" (circular), US1 AC1 (tautological), US3 AC1 "concurrently", FR-012 (unfalsifiable, already true), NFR-001 "well within", FR-013 "once measurement is complete". | **fix now** | Replace each with a number. |
| R10 | requirements | SC-001/003/004 are not CI-verifiable; SC-004 is unobservable server-side (cannot distinguish "stopped" from "between calls"). | **fix now** | Split into automated assertion + named manual run. |
| R11 | requirements | The edge case that actually happened — two sessions on the same agent — has no FR and no acceptance scenario; the loser's reply is unspecified. | **fix now** | Add FR-005a naming the loser's exact reply. |
| R12 | requirements | No observability requirement at all, despite the spec originating in a prod post-mortem. `mark_polled` is per-connection, so N sessions are indistinguishable. | **fix now** | Add FR-014; it also blocks US5 and SC-004. |
| R13 | requirements | Missing: agent unseated mid-session → session flips to `no_game` and may be told to stop mid-match with no explanation. | **fix now** | Add FR-015 with a distinct `stop_reason`. |
| R14 | requirements | Missing: in-flight turn when a match ends early; partial scores; what the match reports. | **fix now** | Add FR-009a. |
| R15 | requirements | Missing: MCP token expiry during a long wait — a known unfixed landmine, and this feature's premise is sessions living for hours. | **fix now — blocker** | Silence is the worst option. Must be an assumption or an explicit out-of-scope. |
| R16 | requirements | The hold-exit reply drops the idle fields (`should_stop`, `idle_seconds`), and the hold loop never re-evaluates the game picture — so US2 AC2 cannot work. | **fix now** | Add FR-003a. |
| R17 | requirements | FR-006b conflicts with Out of Scope ("no new screen") and doesn't name a surface. | **fix now** | Name the surface. |
| R18 | requirements | FR-008 doesn't say where the instructions live; two sources exist (`mcp_tools.py`, `connections_connect_guide.py`). | **fix now** | Enumerate both. |
| R19 | requirements | "Operator" under-defined; the motivating pain is a *user's* zombie match that only an admin can fix. | **fix now** | State the predicate and refusal mode. |
| R20 | requirements | Numbering defects: two Assumptions numbered 6; FR-006a/b break the flat convention. | **fix now** | Editorial. |
| R21 | requirements | Constitution Validation overclaims — FR-004 needs an integration test, and FR-010's marker needs a migration. | **fix now** | Correct the table. |

## Decisions needed before a re-spec

1. **Cost budget.** How many paid model calls may a waiting agent burn per hour?
   This sets the bounded-wait policy and is the load-bearing number for FR-001.
2. **Bounded wait.** Beyond the budget, what happens — a clean "come back at HH:MM"
   stop, or keep holding?
3. **Ratings.** Does a match ended early count toward records and the leaderboard?

## Recorded follow-ups (real gaps, not being built in this PR)

- F6 — the `cancel_match` watchdog race and the forbidden ACTIVE→CANCELLED
  transition are live defects independent of this feature.
- F8 — Practice Arena's 365-day horizon permanently disables the stop hint.
- R15 — hourly MCP token expiry versus long-lived sessions.
