# Admin Engagement Dashboard + Signup-Source Capture: Spec

**Revision 2** — rewritten after the spec checkpoint. Three reviewers (Codex
feasibility, Claude requirements, Claude completeness) returned 30 findings, 12 of
them HIGH. Every HIGH was verified against the real code before being accepted;
the verification notes are in "Review outcomes" at the end.

**Feature:** one admin-only page (`/admin/engagement`) answering two questions
Chris cannot answer today — *where do new users fall out on the way to actually
playing?* and *which traffic source produced the people who played?* — plus the
first-touch capture that makes the second question answerable at all.

**Delivery path:** full Feature Factory. `silent-risk=yes`, `design-settled=no`,
`completeness-risk=yes`. Adds a DB migration, a model change, and new middleware.

---

## Why (the problem)

1. **Acquisition is unmeasurable.** Chris recruits alpha users from Reddit.
   Nothing in the app records where a signup came from, so "did r/hermesagent
   work?" is unanswerable.

2. **Engagement data exists but is never read.** `users.created_at`,
   `connections.first_connected_at`, `players.joined_at`,
   `turn_submissions.submitted_at` already describe the whole journey. No page
   reads them together.

3. **The suspected leak is invisible.** Connecting an AI needs two steps the AI
   cannot do for itself (click through Google sign-in, restart the CLI). Nothing
   surfaces how many people stall there.

---

## Design decisions

### D1 — First touch is captured in middleware, on the first page view

A visitor lands on `/?utm_source=hermesagent`, reads the page, clicks around, and
*then* signs in. By the time the request reaches `/auth/google/login`
([auth.py:84](../../../app/routes/auth.py)) the UTM parameter is gone. Capturing at
the login route records nothing for anyone who did not sign in on their first
click.

A small middleware records first touch on the **first request of a session** and
never overwrites it. The value rides in the session, so it survives later
navigation and the Google OAuth round trip.

### D2 — Middleware ordering: added BEFORE `SessionMiddleware`

Starlette's `add_middleware` **inserts at position 0**, so the *last* middleware
added is the *outermost* — `app/main.py:259` documents this on the last-added
`CanonicalHostMiddleware`.

`FirstTouchMiddleware` must run **inside** `SessionMiddleware` so `request.session`
is populated. Its `add_middleware` call must therefore appear **before** the
`SessionMiddleware` call at [main.py:240](../../../app/main.py).

Placed after, capture records nothing, every signup shows as "direct", and
nothing raises. Two independent reviewers checked this claim and confirmed it.

### D3 — This sets a cookie for anonymous visitors. That is a deliberate choice.

**Chris's decision, 2026-08-13, overriding the non-goal recorded in discovery.**

Today an anonymous visitor gets **no cookie at all**: nothing writes to
`request.session` until sign-in, and Starlette only emits `Set-Cookie` when the
session is modified. Writing first touch on the first page view means every
visitor now receives a session cookie.

Chris chose this over the cookie-free alternatives because full attribution — the
source surviving any amount of browsing before signup — is the point of the
feature.

Consequences, stated plainly rather than buried:
- This is an analytics cookie on an anonymous visitor. In the EU that generally
  needs consent; it is not "strictly necessary". **A consent banner is not part of
  this feature** and remains an open item for Chris.
- The discovery non-goal "no anonymous visitor-ID cookie" is superseded and has
  been updated in `state.json`.
- The session is a **signed cookie, not server-side storage**, with a hard ~4KB
  browser limit. Every captured field is length-capped (D4) so first touch cannot
  push a session over the limit and silently drop it.

### D4 — Source is derived, stored raw, length-capped

Stored on `users`: `first_utm_source`, `first_utm_medium`, `first_utm_campaign`,
`first_referrer_host`, `first_landing_path`.

Derived label precedence: `utm_source` → else `first_referrer_host` → else
`"direct"`.

- Only the referrer **host**, never the full URL — a full URL can carry personal
  data in its query string.
- Every value truncated **at capture time**, before it enters the session cookie,
  not merely at the DB write. The cookie is a consumer too.
- **NULL is not "direct".** A row with no capture at all renders as
  `"unknown"`, kept separate from a genuine direct visit. Otherwise the biggest
  row in the source table would silently mean "we failed to capture this".

### D5 — Funnel step order follows the product's real gate ladder

**Corrected after review (both Claude lenses, HIGH, code-confirmed).** Revision 1
ordered the funnel connection-before-agent. The product's own ladder in
[nav_context.py](../../../app/routes/nav_context.py) is the opposite:

```
NEEDS_HANDLE = 1  →  NEEDS_AGENT = 2  →  NEEDS_MCP_CONNECTION = 3
```

Because each step counts only people who cleared the step above (D7), the wrong
order would truncate every user who followed the real path — inventing a drop at
the connection step and understating the agent step. Exactly the bug class this
spec exists to prevent.

**Final steps:**

| # | Step | Source of truth |
|---|---|---|
| 1 | Signed up | `users.created_at` in window |
| 2 | Picked a handle | `users.handle_key IS NOT NULL` |
| 3 | Built an agent | any `agents` row, `kind='ai'`, ever |
| 4 | AI connected | any `connections.first_connected_at IS NOT NULL`, ever |
| 5 | Joined a match | any `players` row, ever |
| 6 | Played a turn | a `turn_submissions` row with `was_defaulted = false` |
| 7 | Came back another day | played on 2+ distinct days |

### D6 — "Started AI hookup" is dropped as a step

Revision 1 made it step 3. It is broken two independent ways, both code-confirmed:

1. **The rows are garbage collected.** `gc_pending_connections`
   ([pending_connection_gc.py:15](../../../app/engine/pending_connection_gc.py))
   hard-deletes incomplete `ConnectionSetup` rows after 24 hours, and it runs on
   ordinary page loads. A historical "started and abandoned" count erases itself.
2. **MCP users never create one.** `ConnectionSetup` is written in exactly one
   place — `connections_machine_setup.py:106`. The MCP path builds a `Connection`
   directly ([mcp_connection.py](../../../app/engine/mcp_connection.py)). Every MCP
   user would fail this step and, under strict nesting, vanish from every step
   after it.

The leak it was meant to show survives as the drop between **built an agent** and
**AI connected**, both of which read durable rows.

A separate live counter — *incomplete setups right now* — is shown as a summary
number, explicitly labelled as a 24-hour snapshot, not a historical total.

### D7 — Strict nesting, and "ever did X"

A user counts at step N only if they cleared steps 1..N-1.

Step membership is **ever did X**: archived agents and deleted connections still
count. Filtering on `archived_at IS NULL` is what made the funnel go *up* in the
middle during design (11 users had connected an AI, 13 had joined a match).

**Known limit, accepted:** `delete_match` and `reset_handle` are real deletes, so
a past funnel is not perfectly reproducible after an admin destroys data. Fixing
that needs an append-only event log, which is out of scope. The page carries a
one-line note saying so.

### D8 — Internal users are marked by a stored flag, set at creation

Keying the exclusion off email is unstable: `sync_google_user` **rewrites
`users.email`** on later logins ([auth.py:52–79](../../../app/routes/auth.py)), so a
user could drift in and out of the excluded group between page loads.

**A stored `users.is_internal` boolean**, set at account creation, never
recomputed. Every query filters on that one column.

**Set at creation in all three places a user row is born** — not only the
backfill:

| Site | Rule |
|---|---|
| `auth.py:41` (Google sign-in) | domain rule |
| `bots/seating.py:51` (house bots user) | always `True` |
| `dev_login.py:51` (dev login) | always `True` |

**Why the flag carries the weight.** Filtering bots by `agents.kind != 'ai'` is
necessary but nowhere near sufficient. Measured in the dev DB:

| Account | Agent kind | Player rows |
|---|---|---|
| `ludumlabs@house.local` | **ai** | 264 |
| `ludumlabs_flat_6@house.local` | **ai** | 120 |
| `ludumlabs_no_repeats@house.local` | **ai** | 120 |
| `bots@agentludum.local` | bot | 45 |
| `sims@agentludum.local` | **ai** | 40 |
| `harness-A/B/C@local.test` | **ai** | 8 each |

Only one is marked a bot. The house, sim and harness accounts run agents marked
real AI and are over 500 of 646 player rows. Without the flag the dashboard
measures Chris, not his users.

Backfill seed domains: `agentludum.local`, `house.local`, `local.test`; plus any
`ADMIN`-role user at migration time.

**Correctable, and findable.** The backfill is a guess about today's domains.
So:
- `/admin/users/{id}` gets a toggle. It must render **outside** the
  `{% if not floor_admin %}` block at
  [user_detail.html:16](../../../app/templates/admin/user_detail.html) — floor admins
  are precisely the accounts the backfill flags, and that gate would hide the fix
  button on exactly the rows that need it.
- `/admin/users` gains an "internal" column so a mis-flagged account can be found
  at all.
- `AdminAction` is a closed enum ([admin_audit_log.py:15](../../../app/models/admin_audit_log.py))
  with five values; the toggle adds `mark_internal` (13 chars, fits the 16-char
  column) so the action can be audit-logged like its neighbours.

### D9 — Every count is distinct users, never rows

One user owns several agents and many player rows. Any "how many people" number is
`COUNT(DISTINCT users.id)`. The funnel is safe by construction (D7 uses sets); the
exposed risk is the **source table**, where a naive join would report one busy user
as dozens.

### D10 — "Played a turn" excludes no-shows

When a player misses a deadline the game **writes a submission row for them**
marked `was_defaulted=True` ([scoring.py:196](../../../app/games/hoard_hurt_help/scoring.py)).
In the dev DB that is 4,614 of 20,276 rows — 23%.

"Played a turn" means `was_defaulted = false`. `connections.turns_played` is
**not** used: it is a per-connection counter, not per-user, and the two would
disagree.

### D11 — One timezone for the whole page

The window controls are timezone-aware (copied from `/admin/reports`), so
"came back another day" must use **the same timezone**, not UTC. A US evening
session spans two UTC days and would read as a return visit that never happened.

### D12 — Smoke-test matches excluded

`/admin/reports` already filters matches named with `TEST_NAME_PREFIX`
([admin_reports.py:109](../../../app/read_models/admin_reports.py)). This page uses the
same filter, or the two admin pages report different numbers for the same week.

### D13 — Crawlers need no special handling

Capture only reaches a `users` row on completed Google sign-in. Crawlers never get
that far.

### D14 — No visitor count on this page

Counting anonymous visitors needs a third-party analytics tool — a paid external
service and Chris's decision, not this feature's. The funnel starts at **signed
up = 100%**, with a one-line note saying visitor numbers arrive when analytics is
added.

---

## What we are building

### 1. `app/identity/first_touch.py` — capture

- `FirstTouchMiddleware`: if `session["first_touch"]` is absent, record
  `{utm_source, utm_medium, utm_campaign, referrer_host, landing_path, at}` from
  the query string and `Referer`. Never overwrite.
- Skips non-page requests. **Real prefixes**, verified: `/static`, `/healthz`,
  `/api`, `/mcp`, `/openapi.json`. (Revision 1 listed `/sse`, which does not
  exist — streams live at `/games/{game}/matches/{id}/stream`.)
- Ignores a `Referer` on our own host — internal navigation is not a source.
- Truncates every field at capture time (D3, D4).
- **Advisory only**: wrapped so a failure logs and continues, commented
  `# fail-open: advisory only` per the repo's fail-loud rule.
- `clear_session` ([auth/session.py:25](../../../app/auth/session.py)) currently pops
  only the user key, so first touch would survive a sign-out and be inherited by
  the next signup in a shared browser. It must clear `first_touch` too.

### 2. `app/models/user.py` + migration — storage

Five nullable `String` columns: `first_utm_source(120)`,
`first_utm_medium(120)`, `first_utm_campaign(120)`, `first_referrer_host(255)`,
`first_landing_path(255)`.

Plus `is_internal: bool`, non-null, default `False`, server default `false`.

Additive and reversible. `op.batch_alter_table` for any constraint operation —
SQLite dev DB (see `tests/test_migrations.py`).

**The `is_internal` backfill is the data-critical step.** It must run as an
explicit `UPDATE`, match domain suffixes case-insensitively, and be verified after
deploy by reading back the flagged row count against the known internal accounts
*before* any number on the page is trusted.

### 3. `app/routes/auth.py` + the two MCP callers — write at account creation

`sync_google_user` has **three** callers, not one: `auth.py:106`,
`mcp_server/oauth_auth.py:156`, `mcp_server/connection_identity.py:176`. The MCP
callers create accounts too, and have no web session.

- Signature gains an optional first-touch argument, written **only** on the
  new-user branch (auth.py:41). A returning user's source is never overwritten.
- MCP-created users record source `"mcp"` — a real, distinct label, so an MCP
  signup is never silently counted as direct web traffic.

### 4. `app/read_models/engagement_funnel.py`

Ordered steps per D5, strict nested sets, distinct users, with the drop from the
previous step. Plus the stuck list: each non-returning user with their furthest
step.

Stuck-list display: users with no handle show `email` (admin-only page, so this is
already visible at `/admin/users`); the list is capped at 50 rows with a count of
any remainder.

### 5. `app/read_models/signup_sources.py`

Per derived label: signups, how many played (D10), percentage. Distinct users
(D9). Sorted by signups descending.

### 6. `app/routes/admin_engagement.py` + template

`GET /admin/engagement`, behind `require_platform_admin`, with the same
date-window controls as `/admin/reports`.

**The four summary numbers, named** (revision 1 referenced them without ever
saying what they were):

1. New signups in the window
2. Users who played a turn in the window
3. Turns played in the window (non-defaulted)
4. Incomplete connection setups **right now** — labelled as a 24-hour snapshot (D6)

Each shows a comparison against the immediately preceding window of equal length.
When the window is unbounded (the default), no comparison is shown — "previous
period" is undefined there.

Then: the funnel, the source table, the stuck list.

### 7. `app/templates/base.html`

"Engagement" in the Platform admin submenu next to Match Admin and Reporting
([base.html:95](../../../app/templates/base.html)).

### 8. `app/routes/admin_web.py` + `user_detail.html` + `users_list.html`

The `is_internal` toggle (outside the `floor_admin` gate), the new `AdminAction`
enum value, and an "internal" column on the users list. See D8.

---

## Acceptance criteria

The authoritative list is the discovery checklist in `state.json`, updated in
lockstep with this revision. Summarised:

1. `/admin/engagement` exists, is in the admin menu, 403s for a non-admin.
2. Funnel strictly nested; a test asserts it never increases, including the
   archived-agent case.
3. Steps in gate-ladder order (D5); a test asserts a user who built an agent but
   never connected is counted at "built an agent".
4. Step membership is "ever did X".
5. Bots and internal users excluded everywhere via the stored flag (D8).
6. `is_internal` set at all three user-creation sites, not only the backfill.
7. First touch survives navigation and the OAuth round trip; end-to-end test.
8. First touch cleared on sign-out.
9. MCP-created users record source `"mcp"`, never `"direct"`.
10. "Played a turn" excludes `was_defaulted` rows (D10); test with a no-show.
11. Every people-count is distinct users (D9); test with a multi-agent user.
12. Retention uses the window's timezone, not UTC (D11).
13. Smoke-test matches excluded (D12).
14. Source table: source → signups → played. NULL renders `"unknown"`, not
    `"direct"`.
15. Stuck list labels handle-less users and caps at 50.
16. Funnel starts at signed up = 100%; no visitor count; note explaining why.
17. Migration additive, reversible, batch-mode, existing users NULL source.
18. `/admin/users/{id}` toggles `is_internal`, renders for floor admins, and is
    audit-logged; `/admin/users` shows the flag.
19. Capture is advisory and never breaks a request.

## Non-goals

1. No third-party analytics service.
2. No consent banner — an open item for Chris, created by D3, not solved here.
3. No append-only event log to make deleted data reproducible (D7 limit).
4. No cohort retention grid — noise at 10–20 users.
5. No public-facing changes beyond the cookie now being set.
6. No changes to `/admin/reports`.

---

## Test plan

**Funnel correctness**
- Nested-set invariant: every step count ≤ the one above, on generated data.
- The archived-agent regression case.
- **Gate-ladder order:** a user with an agent and no connection counts at "built
  an agent" and stops there.
- A user who joined a match without connecting does not appear at "AI connected".
- Bots excluded; internal users excluded from summary numbers *and* funnel.
- Distinct users: one user, three agents, twelve player rows → counts once.
- No-shows excluded: a defaulted-only player is not "played a turn".
- Retention across a US evening spanning two UTC days is **not** a return visit.
- Smoke-test matches contribute nothing.
- Empty window renders without divide-by-zero.

**Capture correctness**
- Land with `?utm_source=x&utm_medium=y`, navigate, sign in → source recorded.
- External `Referer`, no UTM → host stored.
- Internal `Referer` → treated as direct.
- Second visit does not overwrite; returning user does not overwrite.
- Over-long values truncated at capture, before the cookie.
- Sign out, sign in as someone else → second user does not inherit the first
  user's source.
- MCP sign-in path records `"mcp"`.
- Capture raising does not fail the page request.

**Flag and migration**
- `is_internal` stays put after `sync_google_user` rewrites the email.
- Backfill flags seed-domain accounts, leaves a gmail.com user alone.
- Bots-user and dev-login users are created internal.
- `alembic upgrade head` then `downgrade` clean on SQLite.
- Toggle moves a user in/out of the funnel and writes an `AdminAuditLog` row.
- Toggle renders for a floor-admin target.

---

## Review outcomes

30 findings across three reviewers. Every HIGH was checked against the code before
being accepted — the reviewers' claims were not taken on trust.

**Accepted and fixed:** mutable-email exclusion (Codex) → D8. Row-vs-user counting
(Codex) → D9. Funnel order vs gate ladder (both Claude lenses) → D5. Setup-table
GC and the MCP path having no setup row → D6. Three `sync_google_user` callers →
build item 3. No-show submissions → D10. Toggle hidden by `floor_admin` → D8.
`is_internal` bypassed at two creation sites → D8. Session-cookie truncation → D3.
`AdminAction` closed enum → D8. Finding a mis-flagged account → D8. First touch
leaking across sign-out → build item 1. UTC-vs-window timezone → D11. Smoke-test
matches → D12. NULL-vs-direct → D4. Nonexistent `/sse` skip path → build item 1.
"Server-side session" wording → D3. Unnamed summary numbers and undefined previous
period → build item 6. Stuck-list label and cap → build item 4.

**Accepted as a documented limit, not fixed:** admin deletes make past funnels
non-reproducible (D7) — an event log is out of scope.

**Escalated to Chris and decided by him:** the anonymous-visitor cookie. He chose
full attribution over the cookie-free options; recorded in D3 with its
consequences, and the superseded non-goal updated in `state.json`.

**Verified as correct, not a finding:** D2's middleware-ordering claim, confirmed
independently by both Claude reviewers against `app/main.py:225–263`.
