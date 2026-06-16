# 016 — Human Player: Spec

**Feature:** a signed-in human can join a Hoard-Hurt-Help match and play turns by
hand, alongside AI agents and scripted bots.

**Companions:** `DESIGN.md` (why), `USER_STORIES.md` (stories), `ARCHITECTURE.md`
(how, reuse map), `PLAN.md` (build order).

**Delivery path:** full Feature Factory (this touches the data model + a
migration, so it is **not** a small change per `CLAUDE.md`).

---

## Scope

**In:** join as a human with no setup; play both turn phases (talk + act) by hand
in the viewer under the existing per-turn deadline with a visible countdown;
in-page + out-of-page "your turn" feedback; submission/resolution feedback; mixed
human + agent + bot matches; leave a match.

**Out (v1):** human-hosted or human-vs-human-only matches / matchmaking;
pausing or extending the clock for humans; mobile-tuned play layout; strategy-text
editing for humans; admin tooling beyond what already exists.

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
- **FR-007** A signed-out user who chooses to play is routed through Google
  sign-in and returned to the join action.
- **FR-008** The human's `seat_name` is unique within the match, using the same
  uniquifier the existing seating uses; on a name collision the server suggests a
  tweaked name rather than erroring.
- **FR-009** A human seat is **active immediately** on join — never placed in the
  "waiting for your client to connect" hold (`seat_reserved_until` stays NULL).
- **FR-010** Join is refused with a clear message when the match is full or not in
  a joinable state; if the user is already seated, they are sent to the viewer.

### Playing a turn

- **FR-011** When a seated human's turn opens, a **play panel** renders in the
  viewer's live region for that user only. Spectators and non-seated viewers never
  see it.
- **FR-012** The play panel shows a **countdown** of the seconds remaining in the
  current phase, derived from the turn's `deadline_at`.
- **FR-013** In the **talk** phase the panel offers a single-line public message
  box (capped at the same length agents use) and a Submit; an empty message is
  allowed.
- **FR-014** In the **act** phase the panel offers **Hoard / Help / Hurt**,
  distinguishable **without color alone** (label + shape/icon). Help and Hurt
  require a target chosen from the other players; Hoard takes no target and hides
  the target picker. Self-targeting is rejected.
- **FR-015** A human's submission is recorded through the **same `GameModule`
  verbs** agents and bots use (`validate_move`, `record_message`,
  `record_submission`), writing normal `TurnMessage` / `TurnSubmission` rows with
  `was_defaulted=False`.
- **FR-016** Submitting causes the scheduler to count the human like any active
  player; the turn resolves early once all active players have acted, with **no
  scheduler change**.
- **FR-017** If a human does not submit the act phase by the deadline, the server
  records `HOARD` with `was_defaulted=True` and the "I did not submit a turn"
  message — identical to the agent rule.
- **FR-018** Re-submitting within an open phase replaces the player's pending
  choice. Submitting after the phase resolves is refused with a friendly "that
  turn already resolved" message; nothing is recorded.
- **FR-019** An illegal move (e.g. Help with no target) is rejected with an inline
  fix message and nothing is recorded.
- **FR-020** A match may contain humans, AI agents, and bots simultaneously; a
  phase resolves only when all active players (any kind) have acted or the clock
  expires.

### Feedback

- **FR-021** After a successful submit, the panel switches to a read-only
  "Locked in — waiting for the others" state showing the player's choice; the
  countdown keeps running.
- **FR-022** On turn resolution, the human's action, message, points delta, and
  round outcome appear in the **same feed rendering** used for all players, and the
  scoreboard updates.
- **FR-023** **Out-of-page alert:** when the seated viewer's turn opens, fire a
  browser Notification (when permission is granted), flash the tab title (always-on
  fallback), and play an optional sound. Alerts fire for both phase openings and
  clear on submit or resolution.

### View, manage, leave

- **FR-024** Matches a user joined as a human appear in `/me/matches` with the
  human seat name and link to the viewer.
- **FR-025** Signing in with the seat-owning account on any device shows the play
  panel on that player's turn; a different account never sees it.
- **FR-026** Between turns the human sees the unchanged spectator viewer.
- **FR-027** A human can leave a match before or during play; leaving sets
  `Player.left_at`, after which the scheduler stops waiting on the seat (reusing
  the existing leave path). Already-played turns remain in the record.

### Boundaries / non-regression

- **FR-028** The spectator viewer (feed, scoreboard, live SSE updates, replay
  timeline) is unchanged for anyone who is not the seated viewer on their open
  turn.
- **FR-029** No private per-player intent ("thinking") is exposed to spectators.
- **FR-030** v1 makes any standard scheduled match human-joinable with **no new
  admin flag**. *(Deferred option: a per-match "humans allowed" toggle — an
  additive `Match` column + a create-form checkbox — if later desired.)*

---

## Acceptance scenarios

1. **Join with no setup.** A signed-in user with no agents clicks "Play this
   match" on a scheduled match, accepts the pre-filled name, confirms, and lands
   on the viewer seated — never seeing the agent-create or connect flows.
   *(FR-005, FR-006, FR-009)*
2. **Play a full turn.** When the user's turn opens, the panel appears with a
   countdown; they type a message and submit (talk), then pick Help → a target and
   submit (act); both appear in the feed when the turn resolves with the right
   points. *(FR-011–FR-016, FR-022)*
3. **Beat the clock / miss it.** A user who submits sees "Locked in"; a user who
   ignores the panel has `HOARD` + "I did not submit a turn" recorded after the
   deadline. *(FR-017, FR-021)*
4. **Off-page nudge.** With the tab in the background, the user gets a
   notification + a tab-title change when their turn opens, and it clears when they
   submit. *(FR-023)*
5. **Mixed match.** A match with one human, two agents, and three bots plays to
   completion; the human's turns resolve in line with the others. *(FR-020)*
6. **Spectator unaffected.** A second signed-in user watching the same match never
   sees a play panel and the viewer is byte-for-byte the prior experience.
   *(FR-011, FR-028)*
7. **Leave.** The human leaves mid-match; the next turn resolves without waiting on
   their seat, and the match finishes. *(FR-027)*

---

## Non-functional / constraints

- Server-rendered HTML + HTMX; live updates via SSE-swapped fragments; **no new
  client transport**. The play panel rides the existing `…/live` swap.
- Must work on a phone (functional, not yet tuned).
- All move recording goes through the `GameModule` contract — **no PD-specific
  logic** added to the platform play path.
- Engine (`scheduler*`, `resolver`/`scoring`), SSE transport, and the agent
  HTTP/MCP play path are **unchanged**.
- Preflight Gate (`ruff` + `mypy app/ mcp_server/` + `pytest -q`) green before any
  push. Full suite (migration + model change ⇒ not the small-change lane).

---

## Open questions

1. **Optional thinking field for humans?** Agents submit a private "thinking"
   string. v1 default: humans send empty `thinking`. Confirm we don't want a
   private notes box.
2. **Sound default.** Default the optional turn sound **off** (opt-in) to avoid
   surprising spectators-turned-players? Recommended: off by default.
3. **Per-match "humans allowed" toggle.** v1 says all scheduled matches are
   joinable (FR-030). Confirm we don't need the toggle yet.
</content>
