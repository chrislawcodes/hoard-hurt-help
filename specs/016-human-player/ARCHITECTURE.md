# Human Player — Architecture

How to build the human-player feature by **reusing what already exists**. The
guiding rule: a human is *one more move source*. The match, the turn loop, the
feed, the scoreboard, and the scoring already work for "a player who submits a
talk message and an action." We add a way for that submission to come from a
person via the web, and a way to represent a person as a player.

**Read first:** `docs/platform/AGENT_LUDUM_ARCHITECTURE.md` (the code map),
then this doc's companions `DESIGN.md` / `SPEC.md` / `PLAN.md`.

All file/line references were verified against the current tree during design.

---

## 1. What already exists that we reuse

| Need | Existing component | Reuse as-is? |
|---|---|---|
| Turn loop (open turn, talk → act, wait, resolve, default missing to Hoard) | `app/engine/scheduler.py` + `app/engine/scheduler_turn_loop.py` (`SimultaneousDriver`) | **Yes — no change.** A human seat is just an active player it waits on. |
| Record a player's talk message | `GameModule.record_message(db, turn, player, message, thinking, existing=...)` (`app/games/base.py`, PD impl `app/games/hoard_hurt_help/game.py`) | **Yes.** |
| Record a player's action | `GameModule.record_submission(db, turn, player, move, existing=...)` | **Yes.** |
| Validate a move | `GameModule.validate_move(move, your_agent_id, all_agent_ids)` (pure, public seat names) | **Yes.** |
| Default a missed move to Hoard | `resolve_turn()` materializes `HOARD`, `was_defaulted=True` for any player with no submission (`app/engine/.../scoring.py`) | **Yes — no change.** |
| "All players acted → resolve early" | `_all_submitted()` / `_all_messaged()` count active players vs non-defaulted rows (`scheduler_turn_loop.py`) | **Yes.** A human's row counts automatically. |
| The Player record + scores | `app/models/player.py` (`match_id`, `user_id`, `agent_id`, `seat_name`, `left_at`, scores) | **Yes — one row per human seat.** |
| Talk / action rows | `TurnMessage` / `TurnSubmission` (`app/models/turn.py`) | **Yes.** |
| Game viewer (live + replay) | `GET /games/{game}/matches/{match_id}` → `game.html`; live fragment `…/live` → `fragments/live_region.html`; PD feed `fragments/pd_live_region.html`, `turn_block.html` (`app/routes/web_viewer.py`) | **Extend** — render a play panel into the live region for the seated viewer. |
| Live updates | SSE `GET …/stream` → `app/broadcast.py` events `turn_opened` / `turn_talked` / `turn_resolved` / `round_ended` / `game_completed`; browser re-fetches `…/live` and swaps `#live-region` | **Yes — no new transport.** The panel appears/disappears on the existing swaps. |
| Join flow scaffolding, "My matches" | `app/routes/web_player.py` (`/games/{game}/matches/{id}/join`, `/me/matches`, leave) | **Extend / fork a human branch.** |
| Match create + lobby + states | `app/engine/match_creation.py`, `app/routes/web_lobby.py`, `GameState` (`app/models/match.py`) | **Yes.** |
| Auth + signed-in chrome | `require_user` (`app/deps.py`), account pill + "My matches" in `app/templates/base.html` | **Yes.** |
| Action color/shape language | `--hoard` / `--help` / `--hurt` tokens + `.action-card.*` in `app/static/style.css` | **Yes.** |

The whole turn-resolution half of the system needs **zero changes**. The work is
two seams: **representing a human as a player**, and **letting a human's move
arrive over the web**, plus the **play-panel UI**.

---

## 2. The two real decisions

### 2.1 How do we represent a human player?

Today a `Player` always points at an `Agent` (`player.agent_id`, FK to
`agents.id`), and `seat_name` is derived as `"{handle}/{agent.name}"`. Agents
have a `kind` (`ai` | `bot`). Bots are agents with `kind=bot`, no connection, no
provider — the server drives them directly. **A human is the same shape as a bot,
except a person drives it instead of a strategy script.**

**Option A (recommended): a human is an `Agent` with `kind="human"`.**
- Add `"human"` to the `kind` enum (it is a `FlexibleEnumType`, so this is cheap).
- One human-agent per `(user, game)` — the person's competitor identity in that
  game, reused across matches, exactly like an AI agent is reused.
- `provider` is `NULL` (no LLM). Relax the existing CHECK constraint so
  `provider` may be NULL for `kind IN ('bot','human')`, not just `bot`.
- No `Connection`, no `current_version`-style routing. For completed-match
  resolution, create **one frozen `AgentVersion`** per human agent with
  `model="human"`, `strategy_text=""`, so every read path that joins
  `AgentVersion` (history, exports, analysis) keeps working unchanged.
- Treat `kind=human` like `kind=bot` everywhere bots are *excluded* from
  connection/provider machinery: turn routing (`turn_routing.py`), connection
  health/capacity, the "needs connecting" math, and the agent setup pages. A
  human never polls and never has a connection, so it must never appear in
  provider-coverage or seat-capacity calculations.

*Pros:* maximum reuse. `Player`, `seat_name`, the feed, the scoreboard, history,
exports, and analysis all work with no per-feature special-casing. The move-record
verbs already take a `Player`. *Cons:* one migration (enum value + CHECK relax);
must audit the spots that branch on `kind == "bot"` and decide human's behavior at
each.

**Option B: nullable `Player.agent_id` + a `Player.is_human` flag.**
- Fewer new rows, but every read model that assumes a `Player` has an `Agent` /
  `AgentVersion` (and there are many — viewer, history, exports, analysis) must
  learn to handle `agent_id IS NULL`. More invasive, more places to miss.

**Recommendation: Option A.** It localizes the change to the data model + a
handful of `kind`-aware branches, and leaves the large read surface untouched.
The cost is one migration and a careful pass over `kind == "bot"` call sites.

> This is a **model + schema change**, so the feature is **not** a "small change":
> it runs the full Preflight Gate and the normal delivery path (per `CLAUDE.md`).

### 2.2 How does a human's move arrive?

Two existing entry points already feed the same module verbs:

1. **Agents** → HTTP API / MCP tools → shared play service
   (`agent_play.submit_talk` / `submit_action`). These require a `Connection` and
   an `agent_turn_token` (`turn_token:agent_id:match_id`). **Not a fit** — a human
   has no connection and no token.
2. **Bots** → scheduler calls `auto_submit_bot_phase(...)`
   (`app/engine/bots/service.py`), which does: build move → translate target seat
   name → internal `agent_id` → `module.validate_move` → `module.record_message` /
   `module.record_submission`. **This is the right shape for a human** — a direct
   call into the module verbs, no token, no connection.

**Plan: a thin web adapter that mirrors the bot path, triggered by a human POST
instead of the scheduler.**

- New routes on the human web surface (`app/routes/web_player.py` or a new
  `web_play.py` sibling, registered through `web.py`):
  - `POST /games/{game}/matches/{match_id}/play/talk`
  - `POST /games/{game}/matches/{match_id}/play/act`
- Each route:
  1. `require_user` → resolve the caller's `Player` in this match
     (`user_id == current_user.id`, `left_at IS NULL`). Reject otherwise.
  2. Load the match's current open `Turn`; check it's `ACTIVE`, the phase matches
     the route (talk vs act), and the deadline hasn't passed. On a closed/resolved
     turn, return a friendly "that turn already resolved."
  3. Translate the chosen target (a public seat name) to the internal player —
     reuse the same name→`agent_id` translation the bot service uses.
  4. `module.validate_move(...)` (act only) → on `GameError`, return an inline
     fix message; record nothing.
  5. `module.record_message(...)` / `module.record_submission(...)` with
     `is_connector_fallback=False`, `existing=<the player's row for this turn if
     any>` (so re-select replaces, matching agent/bot behavior).
  6. Commit, then return the **refreshed live-region fragment** so HTMX swaps the
     panel into its "Submitted — you can still change this" state immediately.

Because `record_submission` writes a `TurnSubmission` with `was_defaulted=False`,
the scheduler's `_all_submitted()` counts it on its next 0.25s poll and resolves
the turn once everyone has acted. **No scheduler change.**

**Deadline behavior — auto-submit the current selection (FR-016).** The act panel
always has a current selection, pre-set to **Hoard**. So:

- *Untouched seat:* the human never POSTs. The scheduler's existing
  `resolve_turn()` default materializes Hoard — exactly the desired result, free.
- *Changed-but-not-submitted (near-miss):* to record the player's *selection*
  rather than fall back to Hoard, the panel **auto-POSTs the current selection a
  beat before the deadline**, client-side (the same ~2s buffer the countdown
  uses). If the browser is gone, the server default still applies. This needs no
  server change — the auto-POST hits the same `play/act` route as an explicit
  Submit. Explicit Submit just does it sooner and resolves the phase early.

**Optional reuse cleanup:** the validate → translate-target → record sequence is
now shared by the bot service and the human route. Factor it into one helper
(e.g. `apply_player_move(db, module, turn, player, move/message)`) that both call,
so the two paths can't drift. This is the same "thin adapters over a shared core"
pattern the codebase already uses for HTTP vs MCP play.

---

## 3. The play panel (UI integration)

The viewer already swaps `#live-region` on every SSE turn event by re-fetching
`…/live`. We render the play panel **inside that fragment**, conditional on the
viewer:

- `_game_view_context()` (`app/routes/web_viewer.py`) already computes
  `viewer_player` (the signed-in user's player in this match). Extend the live
  fragment context with: is this viewer a seated human, is there an open turn,
  which phase, the `deadline_at`, whether this player has already submitted a
  non-defaulted row for the current phase, and the **count of active players still
  outstanding** this phase (for the "waiting on N" indicator).
- The PD live fragment (`fragments/pd_live_region.html`) gains a `play_panel`
  partial that renders **only** when `viewer_player` is human + it's their open
  turn. Spectators and non-seated viewers get nothing — the only additive,
  everyone-visible elements are the "waiting on N players…" line and the CTA.
- **Act panel:** Hoard / Help / Hurt cards, **Hoard pre-selected**, each card
  showing its payoff text. Help/Hurt reveal a **type-ahead target picker** (search,
  not a 100-row scroll); Hoard hides it.
- **Talk panel:** a one-line message box, a **Pass** button, and Submit.
- **Countdown:** render `deadline_at` and count down client-side, reusing the
  existing localtime/JS pattern in `base.html`. It **trusts the server** — shows a
  ~2s safety buffer, stops accepting input just before zero, and treats the route's
  response as truth (a late race → "that turn already resolved"). No server
  ticking.
- **Waiting indicator:** the fragment renders "waiting on N players…" from the
  outstanding count; it refreshes naturally on each SSE-driven `…/live` swap.
- **Submit / Pass / auto-submit:** all post to the `play/talk` or `play/act` route
  via HTMX and swap the returned fragment — same mechanic the viewer already uses.
- **Phone-first:** the panel is laid out for a phone in normal-size matches — large
  tap targets for the three actions, thumb-reachable controls, a searchable picker.

### Out-of-page alerts

- The live fragment sets a data attribute on `#live-region` when it's the
  viewer's turn (e.g. `data-your-turn="talk|act"`).
- A small script (added near the existing viewer JS in `game.html`) listens for
  `htmx:afterSwap` on the live region; when the attribute flips on, it fires the
  browser **Notification** (if permission granted), flashes the **tab title**, and
  plays an optional **sound**. When the attribute clears (submitted / resolved),
  it restores the title. No new server events needed — the existing SSE swap is
  the trigger.

### Action distinctness (accessibility)

Reuse the `--hoard` / `--help` / `--hurt` tokens and `.action-card.*` classes, but
each choice carries a **label + payoff number** (e.g. "Help +4 them"), so
Hoard/Help/Hurt are distinguishable without color (a hard rule from the UX skill)
and a first-timer learns the stakes in place — our entire first-turn onboarding.

---

## 4. Join & leave (reuse `web_player.py`)

- **Join:** the current join flow (`/games/{game}/matches/{id}/join`) is
  AI-agent-centric — it gates on having an agent and can put a seat in a
  "waiting for your client" hold (`seat_reserved_until`). For humans we want a
  **no-setup branch**: pick a display name → find-or-create the user's
  `kind=human` agent for this game (+ its frozen human version) → create the
  `Player` with `seat_name` uniquified per match, `seat_reserved_until = NULL`
  (active immediately) → land on the viewer. Reuse `match_creation`-style id and
  the existing seat-name uniquifier; do **not** reuse the connection-coverage /
  capacity gates (humans are excluded, per §2.1).
- **My matches:** `/me/matches` already lists a user's players and shows seat
  names — human seats appear automatically once the `Player` rows exist.
- **Leave — two cases:**
  - *Before the match starts:* free the seat (reuse the existing pre-start leave /
    seat-removal path). No turns were played; the player simply isn't in the match.
  - *During the match:* leaving does **not** remove the seat — it flips it to
    **auto-Hoard for the rest of the match**, kept in the standings (FR-031). This
    is a new nullable column on `Player` (e.g. `autopilot_at`). The scheduler's
    existing per-phase **bot auto-submit pass** (`auto_submit_bot_phase`) is
    extended to also cover human seats with `autopilot_at` set: it records Hoard
    (act) and an empty message (talk) for them **immediately**, via the shared
    `apply_player_move` helper. Because the row is written before `_wait_for_turn`,
    the table is never made to wait on a departed human — yet the seat stays active
    and keeps scoring (+2/turn) to the end. We deliberately do **not** use
    `left_at` here (that means inactive/removed); auto-Hoard is "still playing,
    just on autopilot."

---

## 5. Joinability & match state

- Humans join matches in `SCHEDULED` / `REGISTERING` (same gate as agents). v1
  default: any standard scheduled match accepts human seats — no new flag — which
  keeps admin tooling untouched (`USER_STORIES` G1). If we later want a per-match
  "humans allowed" toggle, it's an additive column on `Match` + a checkbox in
  `matches_user` create form; flagged as a deferred option in `SPEC.md`.
- Mixed human + agent + bot in one match needs **no** engine change — the loop is
  player-kind-agnostic.

---

## 6. What changes, by file (blast radius)

| Area | File(s) | Change |
|---|---|---|
| Model | `app/models/agent.py`, `app/models/player.py`, new migration in `migrations/versions/` | Add `kind="human"`; relax `provider` CHECK to allow NULL for `bot`+`human`; add nullable `Player.autopilot_at` (leave → auto-Hoard). |
| Human identity helper | `app/engine/` (small new module, e.g. `human_player.py`) | Find-or-create the `(user, game)` human agent + frozen human version. |
| `kind`-aware excludes | `app/engine/turn_routing.py`, `connection_health.py`, agent setup pages | Treat `human` like `bot` (excluded from routing/coverage/capacity). |
| Move-in adapter | `app/routes/web_player.py` or new `web_play.py` (+ `web.py` aggregator) | `POST …/play/talk`, `POST …/play/act` → module verbs. |
| Shared move helper | `app/engine/bots/service.py` + the new route | Factor `apply_player_move` used by the bot pass **and** the human route. |
| Auto-Hoard for leavers | `app/engine/bots/service.py` (`auto_submit_bot_phase`) | Also auto-submit Hoard/empty for human seats with `autopilot_at` set, immediately. |
| Join + leave (human branch) | `app/routes/web_player.py` | No-setup human join (active seat); pre-start leave frees seat, in-match leave sets `autopilot_at`. |
| Viewer context | `app/routes/web_viewer.py` | Add open-turn + viewer-submitted + outstanding-count info to the live fragment context. |
| Templates | `app/templates/fragments/pd_live_region.html` (+ `play_panel` partial), `app/templates/game.html`, lobby/viewer "Play" CTA | Panel (default-Hoard, payoff cards, type-ahead picker, Pass), countdown, "waiting on N", alert JS, CTA. |
| Styles | `app/static/style.css` | Play-panel + action-choice styles (phone-first), reusing existing tokens. |
| Tests | `tests/` | Engine: a human submission resolves a turn; an untouched human defaults to Hoard; a leaver auto-Hoards immediately and isn't waited on. Web: play routes auth + phase/deadline guards; near-miss auto-submit records the selection. |

**No change** to: `scheduler_turn_loop.py` (the loop itself), `resolver`/`scoring`,
the SSE transport (`sse.py` / `broadcast.py`), the scoreboard/feed read models, or
the agent HTTP / MCP play path. (The only engine touch is extending the existing
bot auto-submit pass to cover leavers.)

---

## 7. Invariants we must not break

- **Spectator view stays clean.** The play panel renders only for the seated
  viewer on their open turn. The only additive, everyone-visible elements are the
  "Play this match" CTA and the neutral "waiting on N players…" line (no names, no
  choices — safe for a simultaneous game).
- **No private intent leaks.** A human's "thinking" is empty in v1 and, like
  agents', is never shown to spectators.
- **The clock is the clock.** No pausing/extending. The safe Hoard default removes
  the *penalty* of the clock without changing it, which is what lets the loop stay
  untouched.
- **Safe default + auto-submit selection.** Hoard is pre-selected; the clock
  records the current selection (Hoard if untouched). A near-miss never silently
  becomes Hoard.
- **Humans never enter provider/connection math.** A `kind=human` agent must be
  excluded from routing eligibility, connection health, and seat-capacity counts,
  the same way bots are — or it could distort agent seat limits.
- **Re-select replaces, resolved locks.** Matches agent/bot semantics so behavior
  is uniform across player kinds.
- **A leaver keeps playing on autopilot.** In-match leave sets `autopilot_at`, not
  `left_at`: the seat stays ranked and auto-Hoards (submitted immediately, never
  waited on) to the end.
