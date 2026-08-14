# Plan — Admin Engagement Dashboard + Signup-Source Capture

Implementation plan for `spec.md` revision 4. The spec holds the *why* and the
decisions (D1–D14); this holds the *how*: concrete shapes, build order, and what
each slice must prove before the next starts.

**Delivery path:** full Feature Factory (Chris's call, reaffirmed after the spec
stage). Eight slices, each with its own diff checkpoint.

---

## Architecture at a glance

Three independent pieces that meet only at the read models:

```
  event happens ──► recorder ──► user_milestones (durable, append-only)
                                          │
  visitor lands ──► middleware ──► session cookie ──► users.first_* columns
                                          │                    │
                                          └──────► read models ◄┘
                                                        │
                                              /admin/engagement
```

The recorder and the capture middleware never read each other. The page is the
only consumer of both.

---

## Data model

### New table `user_milestones`

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `user_id` | int FK → `users.id`, `ondelete="CASCADE"` | indexed |
| `milestone` | `String(32)` | `FlexibleEnumType`, matching the repo's existing enum handling |
| `reached_at` | `DateTime(timezone=True)` | UTC. Every windowed count depends on this |
| `agent_kind` | `String(16)` nullable | captured at `set_up_a_way_to_play`; the `agents` row is hard-deleted, so the AC7 denominator is unrecoverable otherwise |
| `source_match_id` | `String(32)` nullable | captured at `joined_match` / `played_turn`; enables read-time smoke-test exclusion (AC17) |

`UniqueConstraint(user_id, milestone)` — the idempotency guarantee.
Index on `(milestone, reached_at)` — every page query filters that way.

**Milestone values:** `signed_up`, `picked_handle`, `set_up_a_way_to_play`,
`ai_connected`, `joined_match`, `played_turn`. Note `returned` is deliberately
**absent** — it is derived at read time (spec D1).

### New columns on `users`

`first_utm_source(120)`, `first_utm_medium(120)`, `first_utm_campaign(120)`,
`first_referrer_host(255)`, `first_landing_path(255)` — all nullable.
`first_source_channel(16)` nullable — holds `"mcp"`, kept out of the UTM columns
so a real `?utm_source=mcp` cannot collide.
`is_internal` — `Boolean`, `nullable=False`, `server_default=false`.

### New config keys

`FIRST_TOUCH_CAPTURE_ENABLED: bool = False` — the deploy gate (spec D8).
`INTERNAL_EMAIL_DOMAINS: str = "agentludum.local,house.local,local.test"` —
parsed to a set, exactly like the existing `platform_admin_emails_set` property.
`dev@localhost` is handled by `dev_login.py` always flagging internal, not by the
domain list (a round-3 finding: it is an address, not a domain).

---

## The mechanism — rewritten after the plan review, using shapes that were run

Plan revision 1 specified two shapes that **do not work**. Both were caught by
reviewers executing them against this repo's SQLAlchemy 2.0.50 + aiosqlite stack,
and both failed **silently** — the caller's save reported success and zero rows
landed. What follows is the verified-working replacement.

### What was wrong, so nobody re-derives it

| Plan v1 said | What actually happens |
|---|---|
| Collect in `after_insert`, write in **`after_commit`** | Raises `InvalidRequestError: session is in 'committed' state`, persists **0 rows** — and because that is a `SQLAlchemyError`, the fail-open handler swallows it. The top three milestones would never record at all |
| Guard the write with **`db.begin_nested()`** | A **no-op inside a flush** — `_prepare_impl` skips its flush loop when `session._flushing`. The row is inserted unprotected and a duplicate raises `IntegrityError` out of the caller's commit: exactly the failure the savepoint exists to prevent |
| One `async def record_milestone` for every caller | All three listener hooks are **sync callbacks**. An async recorder cannot serve them |
| Register listeners "at app startup" | The test client uses `ASGITransport` with no lifespan, so startup never runs — every test would have run with no listeners attached, passing and proving nothing. `event.listen` also rejects `async_sessionmaker` and `AsyncSession` |

### The working shape

**Listener point: `after_flush_postexec`.** `after_insert` collects into
`session.info`; `after_flush_postexec` performs the writes. Not `after_commit`
(refuted above) and not `before_commit` (fires before the collection exists).

**Write mechanism: Core insert on the connection, inside a real savepoint.**

```python
def _record_sync(session, rows) -> None:
    """Sync recorder for listener paths. Advisory: never raises to the caller."""
    conn = session.connection()
    for row in rows:
        try:
            with conn.begin_nested():        # a REAL savepoint at this level
                conn.execute(insert(UserMilestone), row)
        except IntegrityError:
            pass                             # already recorded — the guard
        except (SQLAlchemyError, ValueError):
            logger.exception(...)            # fail-open: advisory only
```

`ValueError` is caught deliberately: `StringLengthExceeded` is a `ValueError` and
would otherwise escape the advisory catch.

**Two entry points, one implementation.** `_record_sync` for the three listener
milestones; a thin `async def record_milestone(...)` wrapper for the three
explicit call sites, which run in normal async request context where
`db.begin_nested()` *does* work.

**Registration at import, not startup** — mirroring the existing
`install_sqlite_parity_guards` pattern, and bound to the `Session` class rather
than the async session factory, so it is active in every test fixture too.

### The dev/prod divergence that must be tested for

A savepoint write **survives a session closed without committing on SQLite, but
not on Postgres** (measured both in-memory and file-backed). Tests run SQLite;
production runs Postgres. So a missing commit **passes every test and loses rows
in production.**

This lands squarely on `ai_connected` at `connection_activity.mark_seen`, which
commits itself and is reached from `require_connection` on read-only endpoints.
Slice 2 must assert from a **fresh session after commit**, never from the writing
session — otherwise the checkpoint passes against a broken implementation.

### Mapping

| Model inserted | Milestone | Extra captured |
|---|---|---|
| `User` | `signed_up` | — |
| `Agent` kind `ai` | `set_up_ai_agent` | — |
| `Agent` kind `human` | `set_up_human_play` | — |
| `Player` **where the user is not the house bots user** | `joined_match` | `source_match_id` |

**Two setup milestones, not one.** Plan v1 had a single
`set_up_a_way_to_play` carrying an `agent_kind`. Because new users default to
manual play, almost everyone would record `human` first, and the write-once rule
means later building an AI agent records nothing — collapsing the AC7 denominator
toward zero. Two separate milestones remove the problem entirely. "Set up a way to
play" is then either of them.

**The `Player` listener must exclude the house bots user.** `bots/seating.py:151`
seats bots as `Player(user_id=bots_user.id)`. Plan v1 excluded bot *Agents* only,
so every bot seat would have recorded a `joined_match` for the house account.

**Stated limits:** `after_insert` does not fire for Core bulk inserts (no current
code does that; slice 2 asserts the events fire so a future bulk insert breaks a
test rather than the dashboard). And postexec writes must be guarded against
re-entrancy or they hit `FlushError` after 100 flushes.

### Explicit recorder calls (field updates, not inserts)

| Milestone | Sites |
|---|---|
| `picked_handle` | `handle_web.py`, `dev_login.py` |
| `ai_connected` | `connection_activity.mark_seen` + the four `mcp_connection.py` branches at 145/174/206/222 |
| `played_turn` | `agent_play.submit_action:269` (the genuine AI path) and `web_play.py:253` (human play). Plan v1 said "two request-level hooks" without naming them; `record_submission` has five call sites and the genuine AI path is unreachable from `record_player_action` |

---

## Build order

Each slice ends green (`ruff`, `mypy`, `pytest`) and gets a diff checkpoint.

| # | Slice | Depends on | Must prove |
|---|---|---|---|
| 1 | Models + schema migration (no backfill). **Includes registering `UserMilestone` in `app/models/__init__.py`** | — | `upgrade`/`downgrade` clean on SQLite; the table exists in a `create_all` schema (the registry line is what every test schema is built from); existing users read NULL sources and `is_internal=false` |
| 2 | Recorder + listeners | 1 | idempotent; **assertions read from a fresh session after commit**, never the writing session; listeners fire for all three models; a forced `IntegrityError` leaves the caller's transaction usable; listeners active under the test fixture (not just under app startup) |
| 3 | Explicit recorders (handle, connect, play) | 2 | **human path records play** (AC4); **MCP-connect-before-agent records both** (AC5); autopilot, defaulted, connector-fallback and null-timestamp rows record nothing; **bot seats record no `joined_match`** |
| 4 | Internal-account predicate + creation wiring + `is_internal` backfill | 1 | survives email rewrite and promote/demote; backfill and creation rule agree on one fixture set |
| 5 | First-touch capture behind the flag. **Includes `main.py` middleware ordering, the skip-prefix list, `clear_session`, `sync_google_user`'s three production callers and its 14 test call sites, and `first_source_channel`** | 1 | **flag off ⇒ no cookie, nothing stored** (AC0); **middleware is inside `SessionMiddleware`** — the spec's most likely silent failure, and untestable if left implicit; flag on ⇒ survives navigation + OAuth; no overwrite; truncated at capture; cleared on sign-out; MCP records channel |
| 6 | Milestone backfill migration | 1,4 | reconstructs from surviving rows; **excludes autopilot** via `players.autopilot_at`; **dry-run readback against a production copy before merge** |
| 7 | Read models — milestone counts, **`signup_sources`**, stuck list | 1, **4** | distinct users; **internal users excluded (AC9)** — needs slice 4, which plan v1 wrongly omitted; shares suppressed below 20 (AC1b); return detection in the window timezone; smoke-test matches excluded; **`"unknown"` vs `"direct"` (AC14)**; **stuck list labels, caps at 50 (AC18)**; empty DB renders |
| 8 | Page, nav, admin toggle | 7 | **three summary numbers, each with window, population and internal filtering (AC1a)**; **both explanatory notes render (AC19)**; 401 anonymous / 403 non-admin; toggle works both ways via `admin_user_actions.py` and audit-logs both new `AdminAction` values; renders for a floor admin; `users_list.html` header row **and** empty-state `colspan` both 7→8; `admin/dashboard.html` nav surface updated alongside `base.html` |

### Correction: this is NOT inert by default

Plan v1 claimed slice 5 made the whole feature safe to merge. **False, and
verified:** `railway.json` runs `alembic upgrade head` as `preDeployCommand`, and
pushing to `main` auto-deploys. On merge, production receives the new table, both
backfills, the listeners, the page and the admin toggle. **Only the visitor-source
cookie is behind the flag.**

Consequences that must be respected:
- The two backfills are irreversible in practice. Slice 6 requires a dry run
  against a copy of production data, with a row-count readback, before merge.
- The listeners begin writing on every user/agent/player insert the moment this
  deploys, flag or no flag.
- The migration runs while the **old** code is still serving, so it must be
  additive-only — no column drops, no renames.

---

## Test strategy

Fast lane (`pytest -m "not integration"`) must stay green throughout; CI runs the
full suite.

**Highest-value tests, by risk** — these map to the findings that cost the most to
discover:

1. Human player reaches `played_turn` (round 2's decisive finding; the default
   onboarding path was invisible to revisions 1–2).
2. MCP user connecting before building an agent records both, in either order.
3. Milestone survives each of the five deletion sites: setup GC, seat release,
   agent hard-delete, match delete, handle reset.
4. A failing milestone write leaves the caller's transaction usable (round 3's
   empirical finding).
5. Flag off ⇒ no cookie set (the deploy gate).
6. Autopilot rows excluded in **both** live recording and backfill, so no step
   change at the deploy date.
7. One user with three agents and many player rows counts once.

**Previously untested acceptance criteria, now assigned.** The plan review found
seven with no test anywhere — and the three summary numbers had lost their tests in
three consecutive rounds, which is a pattern in my own drafting rather than bad
luck. Each now has an owning slice:

| AC | What it needs | Slice |
|---|---|---|
| AC1a — three summary numbers | one test per number asserting window, population, internal filtering | 8 |
| AC1b — shares suppressed below 20 users | render at n=19 and n=21 | 8 |
| AC6 — counts are independent | a user missing `picked_handle` still counts at `played_turn` | 7 |
| AC7 — AI-connected share | denominator is `set_up_ai_agent` holders, not all signups | 7 |
| AC9 — internal users excluded from the page | the reason slice 7 now depends on slice 4 | 7 |
| AC14 — `"unknown"` vs `"direct"` | a user with no capture and a user with a genuine direct visit render differently | 7 |
| AC18 — stuck list | handle-less user labelled; 51 stuck users → 50 rows + remainder | 7 |
| AC19 — both explanatory notes | present in the rendered page | 8 |

**Fixtures.** A `make_user(internal=False)` helper, and a match factory covering
the cases D5 distinguishes. Note the plan review's correction: connector-fallback
rows reuse `was_defaulted`, so they are **byte-identical** to missed deadlines —
there are two distinguishable row shapes, not four. Both are excluded, so the
behaviour is right, but a test claiming to tell them apart would be vacuous.

**Two test-harness gaps to build, not assume:**
- No test in this repo drives `/auth/google/callback`. AC12's OAuth round trip
  needs a harness written as part of slice 5.
- The migration runs in a subprocess configured by environment variables, so
  "backfill and creation rule agree" cannot be a single test. Slice 4 asserts the
  shared predicate directly; slice 6 asserts the migration's output separately.

**Flag readability:** `FIRST_TOUCH_CAPTURE_ENABLED` must be read **per request**,
not captured at import — `app = create_app()` runs at import, so an import-time
read makes the flag-on tests unrunnable.

---

## Risks carried into implementation

| Risk | Mitigation |
|---|---|
| `after_insert` cannot do async work | Collect in the listener, write in `after_commit`; slice 2 exists to prove this shape before anything depends on it |
| Backfill volume in one migration | Dev DB has 1,648 matches / 20,276 submissions; batch the UPDATE and measure before prod. Railway runs it as `preDeployCommand` while old code still serves, so it must be additive-only |
| Two admin pages disagreeing | `/admin/reports` also filters `state == COMPLETED` and windows on `completed_at`; the engagement page states its own window explicitly rather than claiming parity |
| Extra write on the turn-submission hot path | Only the *first* genuine play writes; the unique constraint short-circuits the rest. Measure in slice 3 |
| Scope | 8 slices, ~26 files. If it needs splitting, slices 1–6 (data) and 7–8 (page) are the natural seam |

## Open item, not blocking implementation

The privacy note for the persistent cookie (spec D8). Implementation proceeds
with the flag off; **turning it on is Chris's separate decision** once the site
has a disclosure. Raised again at the PR, and the flag is what makes that raise
meaningful rather than decorative.

---

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Both MEDIUM findings accepted and fixed in the spec revision. (1) Mutable email: D8 rewritten to store users.is_internal at account creation instead of matching email live, plus an admin toggle to correct a wrong flag. (2) Distinct-user rule: added D9 requiring COUNT(DISTINCT users.id) everywhere, with a multi-agent regression test.
- review: reviews/spec.claude.requirements-adversarial.review.md | status: accepted | note: Round 3. 19 findings (7 HIGH), all code-confirmed. Decisive: D8's privacy obligation was honest but had NO GATE - docs/deploy-railway.md:87 confirms push to main auto-deploys, so 'raised at the PR' and 'shipped to production' are the same instant. Fixed by shipping first-touch capture behind FIRST_TOUCH_CAPTURE_ENABLED, default false, with AC0 and a test asserting no cookie and no storage when off. Also fixed: the AC2-vs-AC16 contradiction (write-once-at-event-time vs read-time timezone) resolved by splitting durable setup milestones from read-time-derived play metrics (D1); AC1 corrected to 401-anonymous / 403-non-admin; summary numbers given their own criterion and tests; small-numbers rule added suppressing shares below 20 users, which the earlier non-goal implied but never applied.
- review: reviews/spec.claude.completeness-adversarial.review.md | status: accepted | note: Round 3. 16 findings (6 HIGH), all verified against real code. Decisive: the spec's call-site list was wrong - human agents are created at human_player.py:101 (not agents_create.py), players at 3 sites, first_connected_at at 6 including connection_activity.mark_seen which sets the field inside a values dict and is invisible to text search. Fixed STRUCTURALLY in revision 4 D3 by recording row-creation milestones from SQLAlchemy after_insert events instead of a hand-maintained call-site list. Also fixed: missing reached_at column (D1), agent_kind and source_match_id columns added so AC7 and AC17 are achievable, reset_handle added to the evidence-destruction table (D4).
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: failed | note: Gemini CLI is non-functional on this machine: IneligibleTierError, Google ended Gemini Code Assist for individuals. Not a transient failure and retry cannot succeed. Substituted with spec.claude.requirements-adversarial (same lens, Claude reviewer, spec 020 path), which ran and is reconciled as accepted.
- review: reviews/spec.gemini.completeness-adversarial.review.md | status: failed | note: Gemini CLI is non-functional on this machine: IneligibleTierError, Google ended Gemini Code Assist for individuals. Not a transient failure and retry cannot succeed. Substituted with spec.claude.completeness-adversarial (same lens, Claude reviewer, spec 020 path), which ran and is reconciled as accepted.
- review: reviews/spec.claude.feasibility-adversarial.review.md | status: accepted | note: Round 3. 16 findings (6 HIGH), all code-confirmed; the reviewer empirically ran the transaction question against this repo's SQLAlchemy rather than reasoning about it. Decisive: 'advisory, never blocking' had no mechanism - db.add alone surfaces IntegrityError at the caller's commit and add+flush leaves PendingRollbackError, so only db.begin_nested() works, and UNIQUE raises rather than no-opping with no dialect-agnostic on_conflict available. Fixed in new section D3a. Also decisive: the D4 backfill OVERCOUNTS rather than producing a floor, because autopilot rows carry was_defaulted=False and a real submitted_at - fixed via players.autopilot_at, with the over-exclusion accepted and stated.
