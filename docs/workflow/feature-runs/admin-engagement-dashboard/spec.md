# Admin Engagement Dashboard + Signup-Source Capture: Spec

**Feature:** one admin-only page (`/admin/engagement`) that answers two questions
Chris cannot answer today — *where do new users fall out on the way to actually
playing?* and *which traffic source produced the people who played?* — plus the
first-touch capture needed to make the second question answerable at all.

**Delivery path:** **full Feature Factory.** Routing recorded in discovery:
`silent-risk=yes`, `design-settled=no`, `completeness-risk=yes`. Not the
small-change lane: this adds a DB migration, a model change, and new middleware.

**Origin:** design session 2026-08-13. Chris asked what an acquisition +
engagement dashboard should look like. Investigation found the site records
**nothing** about traffic sources (no analytics script, no referrer capture, no
UTM handling anywhere in the codebase), while engagement data is already sitting
in the DB unqueried. This spec builds the queryable half and the smallest honest
version of the acquisition half.

---

## Why (the problem)

1. **Acquisition is unmeasurable today.** Chris recruits alpha users from Reddit.
   Nothing in the app records where a signup came from, so "did r/hermesagent
   actually work?" is unanswerable. Every launch post is a shot in the dark.

2. **Engagement data exists but is never read.** `users.created_at`,
   `connection_setups.completed_at`, `connections.first_connected_at`,
   `connections.turns_played`, `players.joined_at`, `turn_submissions.submitted_at`
   already describe the whole journey. No page reads them together.

3. **The suspected leak is invisible.** Connecting an AI needs two steps the AI
   cannot do for itself (click through Google sign-in, restart the CLI). In the
   dev DB, 30 of 178 connection setups were started and never completed. Nothing
   surfaces that number.

---

## Design decisions resolved

Discovery recorded `design-settled=no`. These are the decisions that settle it.

### D1 — First touch is captured in middleware, not at the login route

**Problem:** a visitor lands on `/?utm_source=hermesagent`, reads the front page,
clicks around, and *then* clicks sign in. By the time the request reaches
`/auth/google/login` ([auth.py:84](../../../app/routes/auth.py)) the UTM parameter is
long gone from the URL. Capturing at the login route records nothing for anyone
who did not sign in on their very first click.

**Decision:** a small middleware records first touch on the **first request of a
session** and never overwrites it. The value rides in the existing server-side
session, exactly like `next_after_login` already does, so it survives both later
navigation and the Google OAuth round trip.

### D2 — Middleware ordering: added BEFORE `SessionMiddleware`

Starlette's `add_middleware` **inserts at position 0**, so the *last* middleware
added is the *outermost*. `app/main.py:259` documents this ("Outermost:" on the
last-added `CanonicalHostMiddleware`).

`FirstTouchMiddleware` must run **inside** `SessionMiddleware` so that
`request.session` is populated when it runs. Therefore its `add_middleware` call
must appear **before** the `SessionMiddleware` call at
[main.py:240](../../../app/main.py).

**This is the single most likely silent failure in the feature.** Placed after,
`request.session` is unavailable, capture records nothing, the page shows every
signup as "direct", and nothing raises.

### D3 — Source is derived, stored raw

Store the raw inputs; derive the display label at read time.

Stored on `users`: `first_utm_source`, `first_utm_medium`, `first_utm_campaign`,
`first_referrer_host`, `first_landing_path`.

Derived label precedence: `utm_source` if present → else `referrer_host` → else
`"direct"`. Storing raw means a bad derivation rule can be fixed later without
losing data.

Only the referrer **host** is stored, never the full referring URL — a full URL
can carry personal data in its query string.

### D4 — The funnel is a signup cohort

The date window selects users by `users.created_at`. How far each user got is
measured **ever**, with no time limit on the later steps.

Mixing "signed up this month" with "played this month" would let a user signed up
in June and playing in August inflate a July funnel. Cohort framing is the only
version that reads correctly.

### D5 — Step membership is "ever did X", not "currently has X"

Archived agents, deleted connections, and left matches still count. This is the
bug already hit during design: filtering `agents.archived_at IS NULL` made the
funnel go **up** in the middle (11 users had connected an AI, 13 had joined a
match) because archiving an agent removed a user from one step but not the next.

On top of that, each step is a **strict subset** of the step above it. A user
counts at step N only if they cleared steps 1..N-1.

### D6 — Crawlers need no special handling

Capture only writes to a `users` row, and a row is created only on completed
Google sign-in. Crawlers and scanners never get that far, so they never appear.

### D7 — No visitor count on this page

Counting anonymous visitors needs either a third-party analytics tool (a paid
external service — Chris's decision, not this feature's) or a visitor-ID cookie
(which likely triggers an EU cookie-consent banner). Both are non-goals.

The funnel therefore starts at **signed up = 100%**, and the page carries a
one-line note that visitor numbers arrive when analytics is added.

### D8 — One shared "internal user" filter

A single predicate, defined once and used by every query on the page: a user is
internal if their role is `ADMIN`, **or** their email is in a configurable
exclusion list, **or** their email ends with an internal domain
(default `@agentludum.local`, which is the bots service account).

Defined once because the failure mode is the four summary numbers disagreeing
with the funnel underneath them.

---

## What we are building

### 1. `app/identity/first_touch.py` — capture

- `FirstTouchMiddleware`: on any request, if `session["first_touch"]` is absent,
  record `{utm_source, utm_medium, utm_campaign, referrer_host, landing_path, at}`
  from the query string and the `Referer` header. Never overwrite an existing value.
- Skips non-page requests: `/static`, `/healthz`, `/api`, `/mcp`, `/sse`.
- Ignores a `Referer` whose host matches our own host (internal navigation is not
  a traffic source).
- Truncates every stored string to its column length before it reaches the DB.
- **Advisory only.** Wrapped so a failure logs and continues — a broken capture
  must never break a page render. Commented as `# fail-open: advisory only` per
  the repo's fail-loud rule, which permits exactly this case.

### 2. `app/models/user.py` + migration — storage

Five new nullable columns on `users`, all `String`, all defaulting to NULL:
`first_utm_source(120)`, `first_utm_medium(120)`, `first_utm_campaign(120)`,
`first_referrer_host(255)`, `first_landing_path(255)`.

Additive and reversible. Existing users keep NULL. Any constraint operation must
use `op.batch_alter_table` — the dev DB is SQLite and `alembic upgrade head`
otherwise fails there (see `tests/test_migrations.py`).

### 3. `app/routes/auth.py` — write at account creation

`sync_google_user` gains an optional first-touch argument. It is written **only**
on the branch that creates a new `User` (auth.py:41), never on the returning-user
branch. A returning user's source must never be overwritten.

### 4. `app/read_models/engagement_funnel.py` — the funnel

Returns an ordered list of steps, each with a label, a count, and the drop from
the previous step, computed as strict nested sets over a signup cohort.

Steps: signed up → picked a handle → started AI hookup → AI connected → built an
agent → joined a match → played a turn → came back another day.

"Came back another day" = played turns on 2 or more distinct UTC calendar days.

Also returns the stuck list: each non-returning user with the furthest step they
reached.

### 5. `app/read_models/signup_sources.py` — the source table

Per derived source label: signups in the window, how many played a turn, and the
percentage. Sorted by signups descending.

### 6. `app/routes/admin_engagement.py` + template

`GET /admin/engagement`, behind `require_platform_admin`. Follows the existing
`/admin/reports` shape for the date-window controls. Blocks in order:

1. Four summary numbers with a previous-period comparison.
2. The funnel: label, bar, count, drop — largest drop visually flagged.
3. The source table.
4. The stuck-people list.

### 7. `app/templates/base.html` — the menu

A "Engagement" link in the admin menu next to Match Admin, Reporting, Users.

---

## Acceptance criteria

Authoritative list is the discovery checklist (11 items) in
`docs/workflow/feature-runs/admin-engagement-dashboard/state.json`. Summarised:

1. `/admin/engagement` exists, is in the admin menu, and 403s for a non-admin.
2. Funnel is strictly nested; a regression test asserts the sequence never
   increases, including on a dataset where a user archived their agent.
3. Step membership is "ever did X".
4. Bots (`agents.kind != 'ai'`) and internal users excluded everywhere, via one
   shared filter.
5. First touch survives later navigation **and** the OAuth round trip.
6. An end-to-end test proves it: land with `?utm_source=`, browse elsewhere,
   sign in, assert the `users` row records the source.
7. Source table shows source → signups → played.
8. Stuck-people list names users and their furthest step.
9. Funnel starts at signed up = 100%; no visitor count; note explaining why.
10. Migration additive, reversible, batch-mode, existing users NULL.
11. Capture is advisory and never breaks a request.

## Non-goals

1. No third-party analytics service.
2. No anonymous visitor-ID cookie, no landed-and-bounced tracking.
3. No cohort retention grid — noise at 10–20 users.
4. No public-facing changes.
5. No changes to `/admin/reports`.

---

## Test plan

**Funnel correctness**
- Nested-set invariant: for a generated dataset, every step count ≤ the one above.
- The archived-agent regression case specifically (the bug already hit).
- A user who joined a match without ever connecting an AI does not appear at
  "AI connected".
- Bots excluded: a match full of `kind='bot'` agents contributes zero.
- Internal users excluded from all four summary numbers *and* the funnel.

**Capture correctness**
- Land with `?utm_source=x&utm_medium=y`, navigate to a second page, complete
  sign-in → `users` row has the source.
- Land with no UTM but an external `Referer` → host stored, label falls back to it.
- Internal `Referer` (our own host) → treated as direct.
- Second visit does not overwrite the first.
- Returning user signing in again does not overwrite their original source.
- Over-long UTM values are truncated, not rejected.
- Capture raising an exception does not fail the page request.

**Migration**
- `alembic upgrade head` then `downgrade` runs clean on SQLite
  (`tests/test_migrations.py` already guards this pattern).
- Existing users read back NULL.

**Route**
- 403 for non-admin, 200 for admin.
- Renders with an empty database (no divide-by-zero on 0 signups).

---

## Risks

| Risk | Why it matters | Mitigation |
|---|---|---|
| Middleware added after `SessionMiddleware` | Capture silently records nothing, forever | D2 states the order and the reason; an end-to-end test through the real app catches it |
| Divide-by-zero on an empty window | Admin page 500s on a quiet week | Explicit zero-signup test |
| Exclusion filter applied unevenly | Summary numbers contradict the funnel below | One shared predicate, D8; test asserts both paths use it |
| Session cookie size | Session is a signed cookie; first touch adds bytes | Cap every field length; five short strings only |
| Referrer carries personal data | Privacy | Store host only, never the full URL |
