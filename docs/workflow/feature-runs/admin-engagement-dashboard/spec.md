# Admin Engagement Dashboard + Signup-Source Capture: Spec

**Revision 3** — redesigned. Two review rounds, three reviewers each, produced 57
findings (24 HIGH). Revision 2 patched them one at a time; revision 3 accepts that
the patching was treating symptoms and changes the design.

**Feature:** an admin-only page (`/admin/engagement`) answering *where do new users
fall out on the way to playing?* and *which traffic source produced the people who
played?*, plus the durable records that make both answerable at all.

**Delivery path:** full Feature Factory. `silent-risk=yes`, `design-settled=no`,
`completeness-risk=yes`.

---

## Why revision 3 exists — the root cause

Revisions 1 and 2 modelled engagement as a **strict funnel**: one ordered path,
where a user counts at step N only if they cleared steps 1..N-1. Every review round
broke it, and the breakages had one shared cause.

**1. There is no single path. There are at least four.**

| Path | Shape |
|---|---|
| AI agent, web setup | handle → agent → connection → join → play |
| AI agent, MCP first | connection stamped with **no agent and no handle** |
| **Human manual play** | agent `kind='human'`, **no connection, ever** |
| Bots | house-owned, no real user journey |

Human play is not an edge case — it is **the default for every brand-new user**.
`_default_human_choice` ([web_join.py:113](../../../app/routes/web_join.py)) returns
`True` when a user has no history, and its own comment calls this "the no-setup
path". Under strict nesting, a person who signs up, plays manually every day, and
loves the game renders as *stuck at picked a handle*.

Strict nesting converts every unmodelled path into a fake drop. Round 1 found two
unmodelled paths; round 2 found three more. That is a design failing, not a
detail gap.

**2. The app deletes the evidence a drop-off report needs.** Four confirmed sites:

| What is deleted | Where | Cohort erased |
|---|---|---|
| Incomplete connection setups | `pending_connection_gc.py` (runs on ordinary page loads, not scheduled) | "started hookup, gave up" |
| Held seats, ~15 min | `seat_hold.py:87`, `:119` | "joined, AI never came online" |
| Agents with no game history | `agents_lifecycle.py:158` — archives only if `Player` rows exist | "built an agent, never played" |
| Matches, players, turns | admin `delete_match` | any past funnel |

Those are precisely the abandonment cohorts the feature exists to surface.

**Chris's decision, 2026-08-13:** record milestones durably as they happen, and
present them as independent counts rather than a strict waterfall.

---

## Design decisions

### D1 — Durable milestone records replace derived funnel steps

New append-only table `user_milestones`: one row the **first** time a user reaches
a milestone. `UNIQUE(user_id, milestone)` makes recording idempotent — a second
attempt is a no-op, not an error.

| Milestone | Recorded when |
|---|---|
| `signed_up` | a `User` row is created |
| `picked_handle` | `handle_key` first set |
| `set_up_a_way_to_play` | first `Agent` of kind `ai` **or** `human` |
| `ai_connected` | `first_connected_at` first stamped on a connection |
| `joined_match` | first `Player` row |
| `played_turn` | first genuine submission (D5) |
| `returned` | a genuine submission on a second distinct local day |

Why this fixes both root causes:
- **Deletion-proof.** The milestone row survives the connection setup being cleaned
  up, the seat being released, the agent being hard-deleted, the match being
  deleted. Nothing downstream removes it.
- **Path-agnostic.** No ordering is assumed. An MCP user who connects before
  building an agent records both, in whatever order they happen. A human player
  records `set_up_a_way_to_play` and never records `ai_connected` — and that is
  correct, not a drop.

### D2 — Independent counts, not strict nesting

Each milestone shows: how many users **in the signup cohort** ever reached it.
No truncation, no "must have cleared the step above".

The page presents them in the usual order with counts and the difference between
neighbours, but the difference is labelled **"fewer than the step above"**, not
"lost here" — because with multiple paths those are different claims.

`ai_connected` is additionally reported **as a share of users who chose an AI
agent**, not of all signups, so the human-play default does not read as an AI
setup failure.

### D3 — One recorder module, called from the event sites

`app/identity/milestones.py` exposes `record_milestone(db, user_id, milestone)`.
Every site calls that one function; no site writes the table directly.

Call sites: user creation (3 places, D8), handle set, agent create, connection
first-connect (web **and** MCP), player create, submission write.

**Advisory, never blocking.** A milestone write must never break the request that
triggered it — it is reporting, not game state. Wrapped and commented
`# fail-open: advisory only`, the one exception the repo's fail-loud rule allows.

### D4 — Backfill is partial, and says so

The migration backfills milestones from what today's tables can still prove.
It cannot recover deleted rows, so historical numbers are **floors, not totals**.

The page carries a dated line: *"Milestones before <deploy date> are
reconstructed from surviving records and undercount abandonment."* Without it the
first weeks would silently read as unusually healthy.

### D5 — "Played a turn" means a genuine human-or-agent move

Three kinds of row must not count:

1. **Missed deadlines.** A missed turn still writes a submission marked
   `was_defaulted=True` ([scoring.py:196](../../../app/games/hoard_hurt_help/scoring.py)) —
   4,614 of 20,276 rows in the dev DB.
2. **Null timestamps.** `/admin/reports` already treats a NULL `submitted_at` as
   defaulted ([admin_reports.py:182](../../../app/read_models/admin_reports.py)). Matching
   it keeps the two admin pages from reporting different numbers for one week.
3. **Autopilot.** `bots/service.py` auto-plays for a player who left, and those
   rows are not marked defaulted. An abandoner must not clear "played a turn".

Because the milestone is recorded at write time (D3), the recorder simply is not
called on those paths — no read-time filter to get wrong.

`connections.turns_played` is not used: it is per-connection, not per-user.

### D6 — First touch is captured in middleware, on the first page view

A visitor lands on `/?utm_source=hermesagent`, browses, then signs in. By then the
parameter is gone from the URL, so capture must happen on the **first request of a
session** and ride in the session through the OAuth round trip.

`FirstTouchMiddleware` records `{utm_source, utm_medium, utm_campaign,
referrer_host, landing_path, at}` once and never overwrites.

### D7 — Middleware ordering: added BEFORE `SessionMiddleware`

`add_middleware` **inserts at position 0**, so the last-added middleware is the
outermost — `main.py:259` documents this on `CanonicalHostMiddleware`.
`FirstTouchMiddleware` must therefore be added **before** the `SessionMiddleware`
call at [main.py:240](../../../app/main.py) so `request.session` exists when it runs.

Placed after, capture silently records nothing forever. Confirmed correct by three
independent reviewers.

Skip prefixes, verified against the real routes: `/static`, `/healthz`, `/api`,
`/mcp`, `/openapi.json`, `/.well-known`, `/auth`. (Revision 1's `/sse` does not
exist; `/.well-known` and the OAuth surface are reachable through the catch-all
root mount at `main.py:314`.)

### D8 — This sets a cookie for anonymous visitors, and that has an open obligation

**Chris's decision, 2026-08-13**, superseding the discovery non-goal.

Today a visitor who only browses gets no cookie; one who clicks sign in already
does, because `google_login` writes to the session ([auth.py:85](../../../app/routes/auth.py)).
The change is that **everyone** gets one.

Facts that matter, all confirmed:
- `SessionMiddleware` sets **no `max_age`** ([main.py:240](../../../app/main.py)), so
  Starlette's default applies: a **persistent 14-day cookie**, not a session
  cookie. Attribution therefore cannot survive longer than 14 days, and
  persistent-vs-session is the distinction consent rules turn on.
- The session is a **signed cookie**, not server storage, with a ~4KB browser
  limit. Existing `fresh_connection_key_setup_{id}` entries are written and never
  popped, so the budget is already leaking; first touch must be length-capped **at
  capture time**, before it enters the cookie.
- **The site has no privacy policy and no cookie notice.** Nothing in `app/`.

> **OPEN OBLIGATION — must be resolved before this reaches production.** This
> feature adds a persistent tracking cookie to a site with no disclosure surface.
> Building it is fine; shipping it to real users without a privacy note is a
> decision Chris has to make deliberately. Raised again at the PR.

### D9 — Source is derived, stored raw, length-capped

Stored on `users`: `first_utm_source`, `first_utm_medium`, `first_utm_campaign`,
`first_referrer_host`, `first_landing_path`.

Label precedence: `utm_source` → `first_referrer_host` → `"direct"`.

- Referrer **host** only, never the full URL (query strings carry personal data).
- **NULL renders `"unknown"`, never `"direct"`** — otherwise the largest row in the
  source table silently means "we failed to capture this".
- MCP-created users record `"mcp"` in a **dedicated `first_source_channel` column**,
  not in `first_utm_source`, so a real `?utm_source=mcp` cannot collide with it.
- Known limit, stated on the page: a visitor who arrives from Reddit and whose
  *first completed sign-in* is the MCP OAuth flow is attributed to `mcp`, losing
  the campaign. Fixing it needs first-touch plumbed through MCP OAuth and is out
  of scope.

### D10 — Internal users are marked by a stored flag

Excluding by email is unstable: `sync_google_user` **rewrites `users.email`** on
later logins. Excluding by role is equally unstable: `promote_user` / `demote_user`
change it after the fact.

So: a stored `users.is_internal` boolean, set at creation, **never recomputed** —
not from email, not from role.

Set at every site a user row is born:

| Site | Rule |
|---|---|
| `auth.py:41` (Google sign-in) | configured internal-domain list |
| `app/engine/bots/seating.py:51` | always `True` |
| `app/routes/dev_login.py:51` | always `True` |

**Why the flag carries the weight.** `agents.kind != 'ai'` is nowhere near
sufficient — measured in the dev DB, `ludumlabs@house.local` (264 player rows),
two sibling ludumlabs accounts (120 each), `sims@agentludum.local` (40) and
`harness-A/B/C@local.test` all run agents marked **real AI**. Only
`bots@agentludum.local` is marked a bot. Over 500 of 646 player rows are internal.

Backfill seed domains: `agentludum.local`, `house.local`, `local.test`,
`dev@localhost`. **The backfill and the creation rule must use one shared
predicate** so they cannot disagree.

Those `.local` accounts cannot have come through Google sign-in (Google issues no
token for a `.local` address), so a fourth creation path exists outside the app —
almost certainly `scripts/`. The backfill is therefore the only thing that will
ever flag them, which is why it must be verified after deploy by reading back the
flagged row count against the known internal accounts.

**Correctable and findable:** a two-way toggle on `/admin/users/{id}` rendered
**outside** the `{% if not floor_admin %}` gate ([user_detail.html:16](../../../app/templates/admin/user_detail.html))
— floor admins are exactly the accounts the backfill flags. Plus an `internal`
column on `/admin/users`. `AdminAction` gains **two** values, `mark_internal` and
`unmark_internal` (both under the 16-char limit), matching how every other
reversible admin action is paired.

### D11 — Distinct users, one timezone, smoke tests excluded

- Every people-count is `COUNT(DISTINCT users.id)`.
- "Second distinct day" uses **the page window's timezone**, and the window
  control **defaults to the browser's timezone, not UTC** — a US evening session
  spans two UTC days and would otherwise read as a return that never happened.
  A session crossing local midnight is still a known false positive, accepted and
  noted on the page.
- Smoke-test matches (`TEST_NAME_PREFIX`) excluded, matching
  [admin_reports.py:109](../../../app/read_models/admin_reports.py).

### D12 — No visitor count, no live "incomplete setups" tile

Anonymous visitor counts need a third-party analytics tool — Chris's separate
decision. The milestone list starts at `signed_up`.

Revision 2's "incomplete setups right now" tile is **dropped**: its cleanup job is
not scheduled (it runs only when someone loads `/me/connections` or
`/me/agents/new`), so the number means "however many have accumulated since
someone last visited those pages" — not a snapshot of anything. It also cannot
have a period-over-period comparison, since the rows are hard-deleted.

---

## What we are building

1. `app/models/user_milestone.py` — the append-only table.
2. `app/identity/milestones.py` — `record_milestone`, the single writer (D3).
3. `app/identity/first_touch.py` — `FirstTouchMiddleware` (D6, D7), plus clearing
   `first_touch` in `clear_session` so a second user in the same browser does not
   inherit the first user's source.
4. `app/identity/internal_accounts.py` — the one shared internal predicate used by
   both the creation rule and the migration backfill (D10).
5. `app/models/user.py` + migration — source columns, `first_source_channel`,
   `is_internal`, the new table, and the two backfills (milestones D4,
   internal flag D10). Additive, reversible, `op.batch_alter_table` for constraint
   operations (SQLite dev DB).
6. Recorder calls at the event sites: `auth.py` (+ the two MCP callers in
   `mcp_server/`), `handle_web.py`, `agents_create.py`, `mcp_connection.py`,
   `web_join.py`, and the submission path.
7. `app/read_models/engagement_milestones.py` — counts per milestone over a signup
   cohort, plus the stuck list (users and their furthest milestone; handle-less
   users shown by email; capped at 50 with a remainder count).
8. `app/read_models/signup_sources.py` — source → signups → played, distinct users.
9. `app/routes/admin_engagement.py` + `app/templates/admin/engagement.html`.
10. `app/services/admin_user_actions.py` — the internal toggle, following the
    existing lock / no-op-guard / audit contract that every other admin action
    uses.
11. Nav and admin surfaces: `base.html` (Platform admin submenu),
    `admin/dashboard.html` (the second admin nav surface), `users_list.html`
    (new column — its hardcoded `colspan="7"` becomes 8), `user_detail.html`.
12. `tests/test_mcp.py` — five monkeypatched two-parameter `sync_google_user`
    fakes break when the signature gains an argument.

**The three summary numbers** (revision 2's fourth is dropped, D12): new signups in
the window; users who played a genuine turn in the window; genuine turns in the
window. Each shows a comparison with the preceding window of equal length; when
the window is unbounded (the default) no comparison is shown, because "previous
period" is undefined there.

---

## Acceptance criteria

Authoritative list lives in `state.json`, kept in lockstep with this revision.
Every criterion has a matching test in the plan below.

1. `/admin/engagement` exists, is in the Platform admin submenu, 403s for a
   non-admin.
2. A milestone row is written exactly once per user per milestone; a second
   attempt is a silent no-op.
3. A milestone survives deletion of the row that caused it — setup GC, seat
   release, agent hard-delete, match delete.
4. **A human player (agent kind `human`, no connection) is counted at
   `set_up_a_way_to_play`, `joined_match`, `played_turn` and `returned`.**
5. **An MCP user who connects before building an agent is counted at both, in
   either order, with no invented drop.**
6. Milestone counts are independent — no count is suppressed because an earlier
   milestone is missing.
7. `ai_connected` is also reported as a share of AI-agent users, not of all
   signups.
8. `played_turn` is not recorded for a defaulted submission, a NULL-timestamp
   submission, or an autopilot submission.
9. Bots and internal users excluded everywhere, via the stored flag.
10. `is_internal` set at all three in-app creation sites; backfill and creation
    rule share one predicate.
11. `is_internal` survives an email rewrite **and** a role promote/demote.
12. First touch survives navigation and the OAuth round trip; cleared on sign-out.
13. MCP-created users record channel `"mcp"` in its own column; a real
    `?utm_source=mcp` does not collide.
14. Uncaptured source renders `"unknown"`, never `"direct"`.
15. Every people-count is distinct users.
16. Return detection uses the window timezone, defaulting to the browser's.
17. Smoke-test matches excluded.
18. Stuck list labels handle-less users, caps at 50, shows a remainder count.
19. The page carries the reconstructed-history note (D4) and the MCP-attribution
    limit (D9).
20. Migration additive and reversible; `alembic upgrade head` then `downgrade`
    clean on SQLite.
21. `/admin/users/{id}` toggles `is_internal` both ways, renders for floor-admin
    targets, and audit-logs via the two new `AdminAction` values;
    `/admin/users` shows the column.
22. Milestone recording and first-touch capture are advisory: a failure logs and
    never breaks the request that triggered it.

## Non-goals

1. No third-party analytics service.
2. No privacy policy or cookie notice — **but see D8's open obligation**; this is
   deferred, not dismissed.
3. No first-touch plumbing through the MCP OAuth flow (D9's stated limit).
4. No recovery of already-deleted history — backfill is a floor (D4).
5. No cohort retention grid.
6. No changes to `/admin/reports`.

---

## Test plan

**Milestones**
- Idempotent: recording twice writes one row.
- Survives each of the four deletions in the root-cause table.
- **Human player reaches play and return milestones** (AC4).
- **MCP user connects before agent; both recorded; no invented drop** (AC5).
- Counts are independent: a user missing `picked_handle` still counts at
  `played_turn`.
- Not recorded for defaulted, NULL-timestamp, or autopilot submissions.
- Recorder raising does not fail the triggering request.

**Exclusion**
- Internal user excluded from every number on the page.
- Flag survives `sync_google_user` rewriting the email.
- Flag survives promote then demote.
- Backfill and creation rule agree on the same fixture set.
- Bots-user and dev-login users created internal.
- Toggle moves a user in and out, writes an audit row, renders for a floor admin.

**Capture**
- Land with UTM, navigate, sign in → recorded.
- External referrer stored as host; internal referrer treated as direct.
- No overwrite on a second visit or a returning user.
- Over-long values capped at capture, before the cookie.
- Sign out, sign in as someone else → no inheritance.
- MCP sign-in records channel `mcp`, and `?utm_source=mcp` stays distinct.
- Capture raising does not fail the page.

**Page**
- 403 non-admin, 200 admin.
- Empty database renders with no divide-by-zero.
- Distinct users: one user, three agents, twelve player rows → counts once.
- US evening session spanning two UTC days is not a return.
- Smoke-test matches contribute nothing.
- Both explanatory notes render.

**Migration**
- `upgrade` then `downgrade` clean on SQLite.
- Milestone backfill produces floors consistent with surviving rows.

---

## Review outcomes

57 findings across two rounds and three reviewers (24 HIGH). Every HIGH was
verified against the real code by the orchestrator before being accepted — several
reviewer claims were checked and one was found overstated (revision 2's "anonymous
visitors get no cookie at all"; `google_login` already writes a session).

**Round 1 (30 findings)** produced revision 2: stable internal flag, distinct-user
counting, gate-ladder ordering, dropped setup step, three `sync_google_user`
callers, no-show filtering, timezone and smoke-test consistency.

**Round 2 (27 findings)** showed revision 2's patches were symptom-level, and
produced this redesign. Decisive findings: `AgentKind.HUMAN` is the **default**
new-user path and was invisible to the funnel; held seats and history-less agents
are hard-deleted; the cleanup job is unscheduled so the "24-hour snapshot" tile was
meaningless; the session cookie is persistent for 14 days; the site has no privacy
disclosure at all.

**Escalated to Chris and decided by him:** (1) the anonymous-visitor cookie — full
attribution chosen, D8; (2) the funnel shape — durable milestones with independent
counts chosen over patching strict nesting, D1/D2.

**Verified correct across all three reviewers, not findings:** D7's middleware
ordering; `was_defaulted` being NOT NULL with a `false` server default;
`AdminAction` having room for the new values; the `floor_admin` template gate.

**Record-keeping note.** The round-2 requirements review's machine-readable JSON
block was truncated by one closing brace, so assembly rejected it. The orchestrator
repaired that single character rather than re-running a 130k-token review; the
repaired block parses to 14 findings, matching the count the reviewer reported in
prose. No finding text was added, removed, or altered.

**Reviewer independence caveat.** Round 1 had a cross-vendor lens (Codex) plus two
Claude lenses. Round 2 was three Claude lenses reviewing a Claude-authored spec,
because the Gemini CLI is dead on this machine (`IneligibleTierError` — Google
ended the individual tier). Round 2's agreement is therefore weaker evidence than
round 1's. Codex re-enters at the plan checkpoint.
