# 016 — Human Player: Spec

**Feature:** a signed-in human can join a Hoard-Hurt-Help match and play turns by
hand, alongside AI agents and scripted bots.

**Companions:** `DESIGN.md` (why), `USER_STORIES.md` (stories), `ARCHITECTURE.md`
(how, reuse map), `PLAN.md` (build order).

**Delivery path:** full Feature Factory (this touches the data model + a
migration, so it is **not** a small change per `CLAUDE.md`).

> **Design decisions resolved 2026-06-16** (adversarial UX review): safe Hoard
> default + auto-submit current selection (no auto-pilot toggle); inline payoff
> hints + lean on the safe default for the first turn; phone-first play; free-text
> talk + one-tap Pass; "waiting on N players…" + early resolve; leave = seat
> auto-Hoards to the end; notification permission requested at join.

---

## Scope

**In:** join as a human with no setup; play both turn phases (talk + act) by hand
in the viewer under the existing per-turn deadline with a visible countdown that
defaults to a safe Hoard; in-page + out-of-page "your turn" feedback;
submission/resolution feedback; mixed human + agent + bot matches; phone-first
play for normal matches; leave (seat auto-Hoards to the end).

**Out (v1):** human-hosted or human-vs-human-only matches / matchmaking; pausing
or extending the clock; phone parity for huge (up to 100-player) matches;
strategy-text editing for humans; admin tooling beyond what already exists.

---

## Functional requirements

### Identity & join

- **FR-001** A human player is represented as an `Agent` with `kind="human"`,
  owned by the signed-in user, one per `(user, game)`, reused across matches.
- **FR-002** A `kind="human"` agent has no `Connection` and no provider
  (`provider` is NULL). The DB CHECK that requires `provider` for `kind="ai"`
  must allow NULL for `kind IN ('bot','human')`.
- **FR-003** Each human agent has exactly one frozen `AgentVersion`
  (`model="human"`, `strategy_text=""`) so every read path that joins
  `AgentVersion` keeps working with no special-casing.
- **FR-004** A `kind="human"` agent is excluded from all connection/provider
  machinery — turn routing eligibility, connection health, and seat-capacity /
  provider-coverage math — the same way `kind="bot"` is excluded.
- **FR-005** A signed-in user can join a `SCHEDULED`/`REGISTERING` match as a
  human from (a) the lobby card and (b) the match viewer.
- **FR-006** Joining as a human requires **only** a display name (pre-filled from
  the user's handle) and a confirm. No agent creation, no connection, no API key,
  no strategy prompt.
- **FR-007** The join screen shows the **time commitment** before the user
  commits (e.g. "~N turns · expect to be active ~M min").
- **FR-008** A signed-out user who chooses to play is routed through Google
  sign-in and returned to the join action.
- **FR-009** The human's `seat_name` is unique within the match, using the same
  uniquifier the existing seating uses; on a name collision the server suggests a
  tweaked name rather than erroring.
- **FR-010** A human seat is **active immediately** on join — never placed in the
  "waiting for your client to connect" hold (`seat_reserved_until` stays NULL).
- **FR-011** Join is refused with a clear message when the match is full or not in
  a joinable state; if the user is already seated, they are sent to the viewer.

### Playing a turn

- **FR-012** When a seated human's turn opens, a **play panel** renders in the
  viewer's live region for that user only. Spectators and non-seated viewers never
  see it.
- **FR-013** The play panel shows a **countdown** of the seconds remaining in the
  current phase, derived from the turn's `deadline_at`. The countdown trusts the
  **server**: it displays a small safety buffer, stops accepting input a beat
  before zero, and treats the server's response as the source of truth.
- **FR-014** In the **act** phase, the panel offers **Hoard / Help / Hurt** with
  **Hoard pre-selected by default**, each card showing its payoff in text
  (e.g. "Hoard +2 you", "Help +4 them", "Hurt −4 them") so the actions are
  distinguishable **without relying on color alone**.
- **FR-015** Help and Hurt require a target chosen from the other players via a
  **type-ahead / search picker** (not a long scroll list); Hoard takes no target
  and hides the picker. Self-targeting is rejected.
- **FR-016** When the act-phase clock ends, the server records the player's
  **current selection** (Hoard if untouched). A near-miss (selection changed but
  not explicitly submitted) records the selection, not a fallback. An explicit
  Submit confirms and lets the phase resolve early.
- **FR-017** In the **talk** phase, the panel offers a single-line public message
  box (capped at the same length agents use) **and a one-tap Pass** (send
  nothing). Submitting or Passing counts the player as done immediately. If the
  clock ends untouched, an empty message is recorded.
- **FR-018** A human's submission is recorded through the **same `GameModule`
  verbs** agents and bots use (`validate_move`, `record_message`,
  `record_submission`), writing normal `TurnMessage` / `TurnSubmission` rows.
- **FR-019** The scheduler counts a human like any active player; a phase resolves
  early once all active players (any kind) have acted, otherwise at the deadline —
  with **no scheduler change**.
- **FR-020** During an open phase, the viewer shows a neutral **"waiting on N
  players…"** indicator (no names, no choices) so a pause reads as alive, not
  frozen. Submit/Pass shrinks N and can resolve the phase.
- **FR-021** Re-selecting within an open phase replaces the player's pending
  choice (the clock submits the latest). Submitting after the phase resolves is
  refused with a friendly "that turn already resolved"; nothing is recorded.
- **FR-022** An illegal move (e.g. Help with no target) is rejected with an inline
  fix message and nothing is recorded.
- **FR-023** A match may contain humans, AI agents, and bots simultaneously.

### Feedback

- **FR-024** After a submit while the phase is still open, the panel shows
  **"Submitted — you can still change this until the clock ends."** "Locked" copy
  is used only after the turn resolves.
- **FR-025** On turn resolution, the human's action, message, points delta, and
  round outcome appear in the **same feed rendering** used for all players, and the
  scoreboard updates. A coasted (defaulted-Hoard) turn shows as a normal Hoard,
  not a scolding "you missed."
- **FR-026** **Out-of-page alert:** when the seated viewer's turn opens, fire a
  browser Notification (when permission is granted), flash the tab title
  (always-on fallback), and play an optional sound (**default off**). Alerts fire
  for both phase openings and clear on submit/Pass or resolution.
- **FR-027** Notification permission is requested **at join time**, never during a
  turn (so the prompt never steals the clock).

### View, manage, leave

- **FR-028** Matches a user joined as a human appear in `/me/matches` with the
  human seat name and a link to the viewer.
- **FR-029** Signing in with the seat-owning account on any device shows the play
  panel on that player's turn; a different account never sees it.
- **FR-030** Between turns the human sees the unchanged spectator viewer.
- **FR-031** A human can leave a match before or during play. **Leaving converts
  the seat to auto-Hoard for the rest of the match** — its Hoard is submitted
  immediately each turn so it never makes the table wait — and the seat stays in
  the standings with a "left" marker. Already-played turns remain in the record.

### Mobile

- **FR-032** Play is **phone-first for normal-size matches**: large tap targets
  for the three actions, a thumb-reachable panel, and the type-ahead target picker
  usable on a small screen. Huge (up to 100-player) matches on phone are out of
  v1 scope (FR-015 picker still works, just not tuned for that extreme).

### Boundaries / non-regression

- **FR-033** The spectator viewer (feed, scoreboard, live SSE updates, replay
  timeline) is unchanged for anyone who is not the seated viewer on their open
  turn — except the additive "waiting on N players…" indicator (FR-020) and the
  "Play this match" CTA (FR-005), which are visible to all.
- **FR-034** No private per-player intent ("thinking") is exposed to spectators.
  Humans send an empty `thinking` field (no notes box in v1).
- **FR-035** v1 makes any standard scheduled match human-joinable with **no new
  admin flag**. *(Deferred option: a per-match "humans allowed" toggle — an
  additive `Match` column + a create-form checkbox — if later desired.)*

---

## Acceptance scenarios

1. **Join with no setup.** A signed-in user with no agents clicks "Play this
   match," sees the time-commitment line, accepts the pre-filled name, confirms,
   and lands on the viewer seated — never seeing the agent-create or connect flows.
   *(FR-005–FR-010)*
2. **Coast safely.** A user ignores the panel for a turn; at the deadline the
   server records Hoard and the feed shows a normal Hoard, no penalty copy.
   *(FR-014, FR-016, FR-025)*
3. **Play a full turn.** When their turn opens, the panel shows Hoard selected
   with payoffs; they type a message and submit (talk), then pick Help → search a
   target → submit (act); both appear in the feed with correct points.
   *(FR-012–FR-018, FR-025)*
4. **Near-miss respects intent.** A user changes the selection to Help → Bob but
   doesn't click Submit; at the deadline Help → Bob is recorded, not Hoard.
   *(FR-016)*
5. **Pace stays legible.** While a human deliberates, other viewers see "waiting
   on 1 player…"; the user Passes talk and the phase advances. *(FR-017, FR-020)*
6. **Off-page nudge.** With the tab backgrounded, the user gets a notification +
   tab-title change when their turn opens (permission was granted at join), and it
   clears when they submit. *(FR-026, FR-027)*
7. **Phone play.** On a phone, the user can read the panel, tap an action, search
   a target, and submit within the clock in a normal-size match. *(FR-032)*
8. **Mixed match.** A match with one human, two agents, and three bots plays to
   completion. *(FR-023)*
9. **Spectator unaffected.** A second signed-in user watching never sees a play
   panel; the only additions they see are the CTA and the "waiting on N" line.
   *(FR-012, FR-033)*
10. **Leave.** The human leaves mid-match; their seat auto-Hoards every remaining
    turn without delaying resolution and shows a "left" marker in standings.
    *(FR-031)*

---

## Non-functional / constraints

- Server-rendered HTML + HTMX; live updates via SSE-swapped fragments; **no new
  client transport**. The play panel rides the existing `…/live` swap.
- Phone-first for normal matches (FR-032).
- All move recording goes through the `GameModule` contract — **no PD-specific
  logic** added to the platform play path.
- Engine (`scheduler*`, `resolver`/`scoring`), SSE transport, and the agent
  HTTP/MCP play path are **unchanged**.
- Preflight Gate (`ruff` + `mypy app/ mcp_server/` + `pytest -q`) green before any
  push. Full suite (migration + model change ⇒ not the small-change lane).

---

## Baked-in defaults (resolved)

- Humans send an empty private `thinking` field — no notes box in v1 (FR-034).
- The optional turn sound defaults **off** (FR-026).
- Every scheduled match is human-joinable; no per-match toggle (FR-035).
