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
| `source_match_id` | `String(32)` nullable | captured at `joined_match` / `played_turn`; enables read-time smoke-test exclusion (AC17) |

`UniqueConstraint(user_id, milestone)` — the idempotency guarantee.
Index on `(milestone, reached_at)` — every page query filters that way.

**Milestone values:** `signed_up`, `picked_handle`, **`set_up_ai_agent`**,
**`set_up_human_play`**, `ai_connected`, `joined_match`, `played_turn`.

Two notes on what is deliberately absent:

- **No `agent_kind` column.** Plan v1 had one, carried on a single
  `set_up_a_way_to_play` milestone. Because the milestone is written once and new
  users default to manual play, almost every user would have recorded `human`
  first and the AI denominator for AC7 would collapse toward zero. Splitting into
  two milestones removes both the column and the bug.
- **No `returned` milestone.** It is derived at read time so it can be recomputed
  in the reader's timezone (spec D1).

### New columns on `users`

`first_utm_source(120)`, `first_utm_medium(120)`, `first_utm_campaign(120)`,
`first_referrer_host(255)`, `first_landing_path(255)` — all nullable.
`first_source_channel(16)` nullable — kept out of the UTM columns so a real
`?utm_source=mcp` cannot collide. It carries **three** values, not one:

| Value | Meaning |
|---|---|
| `"mcp"` | created through an MCP sign-in path, no web session existed |
| `"direct"` | capture **ran** and found no campaign tag and no external referrer |
| `NULL` | capture never ran — the flag was off, or the row predates this feature |

Without the explicit `"direct"`, AC14 is unwritable: the spec's precedence rule
(campaign tag → referrer host → `"direct"`) collapses "we looked and they came
direct" together with "we never looked", which is the exact bug AC14 forbids.
`NULL` then renders as `"unknown"` and means one unambiguous thing.

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
`install_sqlite_parity_guards` pattern, so it is active in every test fixture and
not only under app startup (which the test client never runs).

**The two event kinds must be bound to different targets. This is a trap.**

| Event | Kind | Bind to |
|---|---|---|
| `after_insert` | **mapper** event | the model classes — `User`, `Agent`, `Player` |
| `after_flush_postexec` | **session** event | `Session` |

Plan revision 2 said "bound to the `Session` class" for both.
`event.listen(Session, "after_insert", cb)` is **accepted without raising** — it
falls into `_MapperEventsHold` — and then **fires zero times**. Green suite,
permanently empty table: the exact failure mode revision 2 was written to remove,
reintroduced by the sentence written to remove it. Verified by execution.

**Clear the collection after each flush.** `session.info` must be emptied once its
rows are written. Measured without it: 66 insert attempts for 2 milestones across
11 flushes. Revision 2 claimed "the unique constraint short-circuits the rest" —
it does, but only after 64 wasted round trips.

**No re-entrancy guard.** Revision 2 mandated one against `FlushError` after 100
flushes. Measured: 150 flushes and 300 milestones run clean in this shape. A guard
here would be a suppression flag that can silently drop writes — strictly worse
than nothing.

### The test that cannot fail — the real problem, corrected twice

Revision 2 said savepoint writes survive an uncommitted session on SQLite but not
Postgres, and mitigated it by asserting "from a fresh session after commit".
**Both halves were wrong**, and two reviewers independently proved it:

- On **file-backed** SQLite the new shape persists nothing uncommitted. The stated
  quirk does not exist.
- The actual defeater is **`StaticPool`**, which SQLAlchemy uses for in-memory
  SQLite. Every "fresh session" against `reset_db` is the **same underlying
  connection**, so a writer that never commits is still visible. One reviewer
  wrote slice 2's must-prove exactly as revision 2 specified and **it passed
  against an implementation that never commits.**

So the mitigation could not fail, which is worse than having none: it manufactures
confidence. And CI runs no Postgres, so nothing in the suite exercises the dialect
production actually uses.

**What slice 2 does instead** — cheapest first, and at least the first is required:

1. **Assert commit ordering with a spy session.** Wrap the session, record the
   order of `flush` / `commit` / milestone-insert, and assert the insert happens
   inside a transaction that is subsequently committed. This discriminates on
   SQLite and needs no new infrastructure.
2. **A parity guard**, in the style of `app/sqlite_parity.py`, that fails loudly in
   tests when a milestone write is left uncommitted.
3. **One real Postgres test**, as its own slice with a CI service container. This
   is the only thing that exercises the dialect production runs. Currently
   **`railway.json` makes production the first Postgres exercise of this code** —
   which is the actual risk, and worth fixing beyond this feature.

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

**`bots/seating.py:51` is a third `User`-creation site**, measured to record
`signed_up` for the house bots account. It is harmless *provided* slice 4 lands
first, since that account is created `is_internal=True` and every read model
filters on the flag (AC9). Recorded here because "harmless because something else
filters it" is exactly the assumption that stops being true later.

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

**The two backfills are irreversible in practice, so both need a rehearsal — and
"do a dry run" is not a procedure.** This repo already has the pattern, at
`scripts/preview_match_id_migration.py`:

```bash
python3 scripts/preview_match_id_migration.py --db copy.db --dry-run
```

It is read-only by construction and takes a path to a *copy*. Slice 6 ships an
equivalent `scripts/preview_milestone_backfill.py` with the same interface, and
slice 4 does the same for the `is_internal` backfill (spec D10 requires a
post-deploy readback and revision 2 gave it neither a dry run nor one).

**Getting the copy is the gap.** The only documented production database access is
`DATABASE_PUBLIC_URL`, which points at the **live** database — there is no
documented dump-and-restore step anywhere in `docs/` or `MEMORY.md`. Writing that
down is part of slice 6, because a "dry run against a prod copy" nobody knows how
to produce will be skipped.
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
| `after_insert` cannot do async work | Collect in `after_insert`, write in **`after_flush_postexec`** using a Core insert on a connection-level savepoint — the shape verified by execution (see "The working shape"). **Not `after_commit`**, which persists zero rows and is swallowed by the fail-open handler |
| A missing commit loses rows only in production | Savepoint writes survive an uncommitted session on SQLite but not Postgres. Slice 2 asserts from a fresh session after commit; see the open question below on whether SQLite-only tests can catch this at all |
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
- review: reviews/plan.claude.implementation-adversarial.review.md | status: accepted | note: Round 2. VERDICT: the new mechanism WORKS - the reviewer built it and ran it against the real stack (SQLAlchemy 2.0.50 + aiosqlite, app.db.make_engine, parity guards, real models). Rows persist past the caller's commit confirmed from an independent connection; duplicates raise inside the savepoint, are swallowed, and the caller's own rows still commit; it composes with the existing savepoint at mcp_connection.py:267; it fires under conftest's db/reset_db/client fixtures; no re-entrancy at 150 flushes. 3 HIGH accepted, all in the text around the shape: (1) after_insert is a MAPPER event - event.listen(Session, 'after_insert') is accepted without raising and fires ZERO times, so revision 2's 'bound to the Session class' reintroduced the exact green-suite-empty-table failure it was written to remove; fixed with an explicit event-kind table. (2) The dev/prod divergence was stated backwards - file-backed SQLite persists nothing uncommitted; the real defeater is StaticPool on in-memory fixtures, and the reviewer wrote revision 2's must-prove literally and it PASSED against a writer that never commits; replaced with three graded mitigations, spy-session ordering required. (3) plan.md:266 still prescribed after_commit - already fixed before this review landed. MEDIUMs accepted: session.info must be cleared (66 insert attempts for 2 milestones over 11 flushes); bots/seating.py:51 is a third User-creation site; the mandated re-entrancy guard is unnecessary and would be a silent-drop risk; no Postgres coverage anywhere in slices 1-8 while railway.json makes production the first Postgres exercise.
- review: reviews/plan.claude.testability-adversarial.review.md | status: accepted | note: Round 2. 15 findings (4 HIGH), two verified by execution. Decisive and independently corroborated by the implementation lens: revision 2's 'assert from a fresh session after commit' CANNOT FAIL, because app/db.py's in-memory engine uses StaticPool so every fresh session is the same DBAPI connection - correct and broken implementations return identical answers, and CI has no Postgres service. Worse than no mitigation because it manufactures confidence. Replaced with three graded options. Also accepted: coverage re-verified from scratch and still false for a DIFFERENT seven ACs - four partials from round 1 untouched plus three newly exposed (AC4's returned half, AC10's three-creation-sites, AC20's reversibility for the slice 4/5/6 migrations); this is the fourth consecutive round the coverage claim has been false and is recorded as a drafting blind spot. AC14 was unwritable as specified - no column distinguished captured-and-direct from never-captured, and D9's precedence rule PRODUCES the bug AC14 forbids; fixed by making first_source_channel three-valued. Credit recorded: six of the eight mapping rows do discriminate, and after_flush_postexec verified working independently.
- review: reviews/plan.claude.completeness-adversarial.review.md | status: accepted | note: Round 2. 14 findings (5 HIGH). Headline accepted in full: revision 2's fixes were ONE-SIDED - the plan changed and nothing else did. Fixed: the spec still specified set_up_a_way_to_play and agent_kind at four places (now split into set_up_ai_agent/set_up_human_play with the removal rationale recorded); the plan's own risk table still prescribed after_commit 130 lines after refuting it; state.json was still the stale 22-item revision-3 list and is now resynced to 25 criteria including AC0/AC1a/AC1b and the 401-anonymous correction; the milestone split had a write side and no read side, so the union rule, the AC7 denominator and the stuck list's furthest-milestone tie-break are now defined. The dry-run requirement had no procedure: the repo's own read-only precedent scripts/preview_match_id_migration.py --db copy.db --dry-run is now cited as the pattern, an equivalent preview script is required for both backfills, and the genuine gap - no documented way to obtain a prod copy, since DATABASE_PUBLIC_URL is the LIVE database - is now written down as slice 6 work rather than left implicit.
