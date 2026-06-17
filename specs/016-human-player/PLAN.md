# 016 — Human Player: Plan

Build order for the human-player feature. Slices are checkpoint-bounded: each ends
green on the Preflight Gate (`ruff` + `mypy app/ mcp_server/` + `pytest -q`) and is
independently reviewable. Because this changes the data model + ships a migration,
it runs the **full** delivery path (not the small-change lane).

**Companions:** `SPEC.md` (FR-NNN), `ARCHITECTURE.md` (reuse map, file blast
radius), `DESIGN.md`, `USER_STORIES.md`.

**Branch:** `claude/human-player-game-join-9cr40t` (worktree-per-task; rebase on
`origin/main` each session).

---

## Strategy

Reuse first. The turn loop, scoring, feed, scoreboard, SSE, and `GameModule` verbs
already handle "a player who submits a talk message and an action." We add only:
(1) a way to represent a human as a player, (2) a web path for a human's move to
reach the existing verbs, and (3) the play-panel UI. Build the data + server seam
first (provable with engine tests, no UI), then the UI, then alerts and polish.

---

## Slice 0 — Data model: human as an agent kind

*Goal: a human can exist as a player in the schema. No UI yet.*

- Add `"human"` to the `AgentKind` enum (`app/models/agent.py`).
- Add nullable `Player.autopilot_at` (`app/models/player.py`) — set when a human
  leaves mid-match to flip the seat to auto-Hoard.
- Migration in `migrations/versions/`: add the enum value where needed,
  **relax the `provider` CHECK** to allow NULL for `kind IN ('bot','human')`, and
  add `players.autopilot_at`. Use `batch_alter_table` for the CHECK/column ops so
  it applies on SQLite. Provide a working `downgrade()`; verify the up/down
  round-trip and `Base.metadata.create_all` both pass on SQLite.
- New helper `app/engine/human_player.py`: `get_or_create_human_agent(db, user,
  game)` → finds or creates the user's `kind=human` agent + its single frozen
  `AgentVersion` (`model="human"`, `strategy_text=""`). Async, typed.
- Exclude `kind=human` from connection/provider machinery the way `kind=bot` is:
  audit `app/engine/turn_routing.py`, `app/engine/connection_health.py`, and the
  agent setup queries; add `human` to the bot-style exclusions.

**Implements:** FR-001–FR-004.
**Tests:** model round-trip; helper creates exactly one agent+version and is
idempotent; a `kind=human` agent never appears in routing eligibility / capacity.
**Checkpoint:** Preflight green.

---

## Slice 1 — Server move-in path (HIGH-CARE)

*Goal: a human's talk + act can be recorded for the current open turn, and the
scheduler resolves it. Still no panel UI — drive it with tests/curl.*

- Factor the shared move helper `apply_player_move(db, module, turn, player, …)`
  out of the bot service (`app/engine/bots/service.py`): validate → translate
  target seat name → internal id → `record_message` / `record_submission`. Bot
  service calls it; the new route calls it. (Reuse cleanup so paths can't drift.)
- Extend `auto_submit_bot_phase` to also auto-submit Hoard (act) / empty (talk)
  **immediately** for human seats with `autopilot_at` set, via the same helper, so
  a leaver is never waited on but keeps scoring.
- New routes (`app/routes/web_play.py`, registered via `app/routes/web.py`):
  - `POST /games/{game}/matches/{match_id}/play/talk`
  - `POST /games/{game}/matches/{match_id}/play/act`
  - Each: `require_user` → resolve caller's active `Player` in the match → load
    the open `Turn`, assert `ACTIVE` + matching phase + deadline not passed →
    `apply_player_move` with `existing=<player's row this turn>` (re-select
    replaces; this is also the endpoint the panel's near-deadline **auto-submit of
    the current selection** posts to) → commit → return the refreshed live-region
    fragment.
  - Talk route accepts a **Pass** (empty message, counts as done).
  - Closed/resolved turn → friendly "that turn already resolved" (no record).
  - `GameError` → inline fix message (no record).

**Implements:** FR-016, FR-017, FR-018, FR-019, FR-021, FR-022, FR-023, FR-031.
**Tests (engine + web):** a human submission via the route resolves a turn; a
mixed human/agent/bot turn resolves when all act; an **untouched** human defaults
to `HOARD`; a **near-miss** auto-submit records the *selection* (Help → Bob), not
Hoard; Pass records an empty message; re-select replaces; post-deadline submit is
refused; illegal move rejected; non-owner / wrong-account refused (auth); a seat
with `autopilot_at` auto-Hoards immediately and is never waited on.
**Why high-care:** this is the only new write path into turn state — guard auth,
phase, deadline, and ownership tightly. Security pass on this slice.
**Checkpoint:** Preflight green.

---

## Slice 2 — Human join (no-setup branch)

*Goal: a signed-in user can take a human seat from the viewer.*

- Human join branch in `app/routes/web_player.py` (or a sibling): pick display
  name (pre-filled from handle) → `get_or_create_human_agent` → create `Player`
  with uniquified `seat_name`, `seat_reserved_until = NULL` (active now),
  `user_id = current_user.id`. Do **not** run connection-coverage / capacity
  gates.
- Join screen shows the **time-commitment** line (FR-007) and **requests
  notification permission here** (FR-027) — never on a turn.
- Join refusals: full match, non-joinable state, already-seated → viewer.
- Signed-out → Google sign-in → back to join.
- **Leave:** pre-start → free the seat; in-match → set `Player.autopilot_at`
  (seat auto-Hoards to the end, stays ranked with a "left" marker).
- Confirm human seats show in `/me/matches` automatically.

**Implements:** FR-005–FR-011, FR-007, FR-027, FR-028, FR-031.
**Tests:** join creates exactly one player + (idempotent) human agent; seat active
immediately; refusals; pre-start leave frees the seat; in-match leave sets
`autopilot_at` and the seat then auto-Hoards without being waited on.
**Checkpoint:** Preflight green.

---

## Slice 3 — Play panel in the viewer (phone-first)

*Goal: the panel appears for the seated viewer on their open turn and submits via
the slice-1 routes — built phone-first.*

- Extend `_game_view_context()` / the live-fragment context
  (`app/routes/web_viewer.py`) with: is the viewer a seated human, is there an
  open turn, which phase, `deadline_at`, whether this player already submitted a
  non-defaulted row for the phase, and the **outstanding-player count**.
- `play_panel` partial inside `fragments/pd_live_region.html`, gated to the seated
  viewer on their open turn:
  - **Act:** Hoard / Help / Hurt cards with **Hoard pre-selected** and **payoff
    text on each card** (label + number — reads without color). Help/Hurt reveal a
    **type-ahead target picker**; Hoard hides it.
  - **Talk:** message box + **Pass** + Submit.
  - Posts via HTMX to the play routes and swaps the returned fragment.
- **Countdown** from `deadline_at`, client-side (reuse `base.html` time JS), but
  **server-trusting**: ~2s safety buffer, stop input before zero, and
  **auto-POST the current selection** a beat before the deadline (FR-016).
- **"waiting on N players…"** rendered from the outstanding count (FR-020).
- After submit (phase open): **"Submitted — you can still change this"** state
  (not "Locked"); "Locked" only post-resolution.
- Styles in `app/static/style.css`: **phone-first** play-panel + action choices —
  large tap targets, thumb-reachable, searchable picker — reusing
  `--hoard`/`--help`/`--hurt` tokens and `.action-card.*`.
- Add the lobby + viewer "Play this match" CTA.

**Implements:** FR-012–FR-016, FR-020, FR-024, FR-025, FR-029, FR-030, FR-032,
FR-033.
**Tests / verification:** UX-skill Ground checks — preview the viewer with a live
match (`scripts/new_test_game.py`), snapshot + screenshot the panel in each state
(not-your-turn, talk, act with default Hoard, submitted-can-still-change,
coasted-Hoard), resize to **phone width and verify play works there**, and confirm
a non-seated spectator sees no panel (only the CTA + "waiting on N").
**Checkpoint:** Preflight green + screenshots (incl. phone) attached to the PR.

---

## Slice 4 — Out-of-page alerts + polish

*Goal: a human watching another tab doesn't miss their turn.*

- Live fragment sets `data-your-turn="talk|act"` on `#live-region` when it's the
  viewer's turn.
- Script near the viewer JS in `game.html`: on `htmx:afterSwap`, when the
  attribute flips on → browser Notification (if permission was granted at join) +
  tab-title flash + optional sound; on clear → restore title. **Permission is
  requested at join (slice 2), never on a turn.**
- Sound is opt-in, **default off**.
- Empty / error / live-vs-finished states for the panel reviewed and copy
  finalized (microcopy from `DESIGN.md` §5).

**Implements:** FR-026, FR-027 (the alert side; permission request lands in slice 2).
**Verification:** manual — background the tab, confirm notification + title flash
fire on turn open and clear on submit/Pass; confirm graceful behavior when
notifications are blocked (title still flashes; sound only if enabled).
**Checkpoint:** Preflight green.

---

## Slice 5 — Sweep, docs, PR

- Full `pytest` (not just the fast lane), `ruff`, `mypy`.
- Update `docs/platform/AGENT_LUDUM_ARCHITECTURE.md` (the quick-index + data-model
  notes: new `kind=human`, the play routes, the human-join branch) and `STATUS.md`
  if present.
- Security review of the play routes (`/security-review`).
- Open the PR with a `Validation` section listing exact commands + pass/fail and
  the slice-3 screenshots. Do **not** merge — `/ship` only when Chris asks.

**Checkpoint:** CI green; PR ready for review.

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `kind=human` leaks into provider/capacity math and distorts agent seat limits | Slice 0 audits and excludes it like `bot`; tests assert exclusion. |
| New write path is an attack surface (act on someone else's seat) | Slice 1 is high-care: strict `require_user` + seat-ownership + phase/deadline guards; dedicated auth tests + security pass. |
| 60s is too tight for real humans | The safe Hoard default removes the *penalty*; only deviating costs time. If play data still shows it's too tight, the fix is admin-set longer deadlines, not a special human clock. Re-evaluate after the first real match. |
| Near-deadline auto-submit races the server clock | Countdown is server-trusting (buffer + stop-input-early); a lost race returns "that turn already resolved" cleanly. |
| Leaver's auto-Hoard drags the table | Auto-Hoard is submitted **immediately** in the bot pass (not waited on); the seat stays ranked but never delays a phase. |
| Notifications blocked by browser | Tab-title flash is the always-on fallback (sound only if enabled). |
| Migration doesn't apply cleanly on SQLite | Use `batch_alter_table` for the CHECK/column ops; verify round-trip + `create_all` in slice 0. |

## Out of scope (restated)

Human-hosted / human-vs-human matches and matchmaking; clock pause/extend; phone
parity for huge (up to 100-player) matches; human strategy-text editing; new admin
tooling.
