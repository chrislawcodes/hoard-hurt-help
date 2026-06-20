# 017 — Play as Human *and* Send an Agent: Spec

**Feature:** in a single match, a signed-in user can take a human seat (play by
hand) **and** enter one of their AI agents at the same time — so they compete
against their own bot.

**Builds on:** `specs/016-human-player/` (human seat shape) and PR #475
(per-agent cards with embedded AI picker on the join screen).

**Delivery path:** Direct Path. This is **not** a data-model change — the schema
already allows it (see "What already works"). The work is the join handler, the
join screen, and tests. No migration.

---

## Summary

Today the join screen makes you choose **one** of two things: "Play as yourself"
(a human seat) **or** "Send an AI agent." They are mutually exclusive radio
buttons.

Chris wants them to be combinable: tick "Play as yourself" **and** pick an agent
+ its AI, then join once and land in the match holding **two seats** — one human,
one AI. Great for testing your own strategy: does your bot actually beat you?

The data model already supports two seats for one user. The change is mostly
turning an either/or choice into an independent pair, and seating both in one
submit.

---

## What already works (verified in code)

- **A human seat is a `Player` row** tied to a `kind=human` agent (one per
  user-per-game, reused across matches). An AI seat is a `Player` row tied to a
  `kind=AI` agent + a `chosen_provider`. — `app/engine/human_player.py`,
  `app/routes/web_play.py:seat_human_player`.
- **No "one seat per user per match" rule.** Migration
  `0002_allow_multi_agent_per_user` dropped the old `(match_id, user_id)` unique
  constraint. The only seat-uniqueness rules left are `(match_id, seat_name)` and
  `(agent_id, match_id)` — `app/models/player.py`.
- Because the human agent and the AI agent are **different `agent_id`s**, one
  user holding both a human seat and an AI seat in the same match satisfies every
  existing constraint. **No migration is needed.**
- The **only blocker** is `join_submit`: it does `if play_as == "human": seat &
  return`, so the human path and the AI path never run together —
  `app/routes/web_player.py:466`.

---

## Scope

**In:**
- Join one match as a human seat **and** an AI-agent seat in a single submit.
- Join screen lets the two choices coexist instead of being mutually exclusive,
  while keeping the per-agent cards + embedded AI picker from PR #475.
- Both seats count toward `max_players` (you take two of the slots).
- Correct handling when the chosen AI is not live yet (its seat is "held" and the
  user is sent to connect it; the human seat is already active).

**Out (v1):**
- More than one AI agent **plus** a human for non-admins (regular users stay
  capped at one AI agent; admins keep their multi-agent power).
- Any change to how turns are played, scored, or resolved — both seats use the
  paths that already exist.
- Human-vs-human-only matches, matchmaking, or new match kinds.

---

## Functional requirements

### Join

- **FR-001** On the join screen, "Play as yourself" and "Send an AI agent" are
  **independently selectable** — a user may pick neither-blocked, human only,
  agent only, or **both**.
- **FR-002** Submitting with **both** selected seats the user as a human (active
  immediately) **and** enters the chosen agent with its chosen AI, in one POST.
- **FR-003** The join screen keeps the PR #475 design intact: each agent is its
  own card, and the AI picker is embedded inside the selected agent's card.
- **FR-004** A user must select **at least one** of {human, agent}. Submitting
  with neither is a clear, friendly error (no silent no-op).
- **FR-005** The human-seat path stays **idempotent** — re-joining when you
  already hold a human seat does not create a second human seat
  (`seat_human_player` already returns `False` in that case).
- **FR-006** Capacity is checked across **both** seats. If seating both would
  exceed `max_players`, the join is refused with "This match is full" and
  **neither** seat is created (all-or-nothing within the one transaction).

### The "AI not live yet" case

- **FR-007** If the chosen AI is **live**, both seats are created active and the
  user lands on the match viewer.
- **FR-008** If the chosen AI is **not live**, its seat is "held"
  (`seat_reserved_until` set) exactly as today, the human seat is created
  **active**, and the user is redirected to the connect countdown for the held AI
  seat — the same redirect the AI-only path uses now.
- **FR-009** Practice-arena auto-start keeps its current rule: start on join only
  when there is **no held seat**. A human + a held AI does **not** auto-start
  (the held seat would just be released at start).

### Identity & fairness

- **FR-010** The two seats keep separate public names: the human seat shows the
  user's handle (or chosen display name), the AI seat shows the agent's name.
  Neither label reveals that the same user owns both — same privacy model as
  today.
- **FR-011** One user holding both a human seat and their own AI seat is allowed
  in **all** match types, **including ranked**. There is no `match_kind` gate.
  *(Decided 2026-06-20: humans should be allowed on the leaderboard.)*
- **FR-012** Human seats count on the leaderboard like any other seat. Self-play
  (your human vs. your own bot) is accepted as fair play, not blocked.

---

## Join-screen UX

Keep the current top-to-bottom flow and the #475 cards. The single change is that
the first choice is no longer a radio that switches the agent section on and off.

Recommended shape:

```
Enter "Friday Ranked"
3 / 4 players registered · Starts in 12 min

How do you want to enter?  (pick one or both)

[✓] Play as yourself
    You (chrislaw) — make every move by hand. No AI, no setup.

[✓] Also send an AI agent
    ┌─ Hawk  · always defects first round, then mirrors ───────┐
    │   Which AI plays Hawk?   [● Claude ready] [○ Gemini idle] │
    └──────────────────────────────────────────────────────────┘
    ┌─ Olive · tit-for-tat, forgiving ─────────────────────────┐
    └──────────────────────────────────────────────────────────┘
    + New agent

           [ Join as yourself + Hawk (Claude) → ]
```

- "Play as yourself" is a checkbox, pre-checked (matches today's default).
- "Also send an AI agent" is a second checkbox; ticking it reveals the agent
  cards. Picking an agent card implies this box is ticked.
- The submit button summarizes what you'll get: "Join as yourself", "Join as
  Hawk (Claude)", or "Join as yourself + Hawk (Claude)".
- If the user has no agents, the agent checkbox shows the existing "Create one"
  hint and stays unticked; human-only still works in one click.

---

## Backend changes

1. **`join_submit`** (`app/routes/web_player.py`): stop early-returning on
   `play_as == "human"`. Instead read both intents from the form, seat the human
   first (if chosen), then seat the agent(s) (if chosen), in the same
   transaction. Reuse `seat_human_player` and `_seat_user_agent` unchanged.
   Decide the redirect from the combined result: held AI seat → connect
   countdown; otherwise → viewer.
2. **Form contract:** replace the single `play_as` radio with explicit intents.
   Recommended: keep `play_as` for backwards compatibility but add it as a
   multi-value or add a `join_human` flag, so the existing `/play/join` direct
   endpoint and its tests keep working. (Exact field names settled in
   implementation; `seat_human_player` and `_seat_user_agent` are the shared
   seams and do not change.)
3. **Capacity:** seat inside one transaction and let the existing per-seat
   capacity checks fire; if any raises 409, the whole submit rolls back so we
   never create a lone half-join.

No engine, scoring, model, or migration changes.

---

## Decisions

1. **Ranked self-play — ALLOWED.** One user may hold a human seat and their own
   AI seat in any match type, including ranked, and human seats count on the
   leaderboard (FR-011, FR-012). No `match_kind` gate. *(Decided 2026-06-20.)*

2. **Button wording** when both are picked — "Join as yourself + Hawk (Claude)".
   Cosmetic; settled in implementation.

---

## Validation plan

- Unit/route tests (SQLite in-memory, mock AI):
  - Human + agent in one submit creates **two** `Player` rows for the user (one
    `kind=human`, one `kind=AI`).
  - Human-only submit still creates exactly one human seat (no regression).
  - Agent-only submit unchanged (regression).
  - Neither selected → friendly 400, no rows created.
  - Both selected but match has room for only one → 409, **zero** rows created.
  - Both selected, AI not live → human seat active, AI seat held, redirect to
    connect countdown.
  - Practice arena does **not** auto-start when the AI seat is held.
  - Idempotent: submitting human+agent when already holding a human seat does not
    duplicate the human seat.
- Preflight: `ruff` + `mypy` + full `pytest`.
- Manual smoke: real server boots, join screen renders, both seats appear on the
  viewer.

---

## Files in play

- `app/routes/web_player.py` — `join_submit` (the branch to change).
- `app/routes/web_play.py` — `seat_human_player` (reused as-is).
- `app/templates/join.html` — the two-checkbox + cards layout.
- `app/static/style.css` — minor, if the checkbox layout needs it.
- `tests/test_lobby.py`, `tests/test_smart_join_flow.py` — extend with the cases
  above.
