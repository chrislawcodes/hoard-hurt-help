**Verdict on the new mechanism: it works.** I built it and ran it against this
repo's stack (SQLAlchemy 2.0.50 + aiosqlite, `app.db.make_engine`,
`async_sessionmaker(expire_on_commit=False)`, `install_sqlite_parity_guards()`
active, the real `User` / `Agent` / `Player` models). `after_insert` →
`session.info` → `after_flush_postexec` → `session.connection()` +
`conn.begin_nested()` + Core `insert()` persists rows past the caller's commit,
swallows duplicates without breaking the caller, composes with the existing
savepoint at `mcp_connection.py:267`, and fires under the repo's own `db`,
`reset_db` and `client` fixtures. Nothing leaks when the caller never commits.
That is a genuine clean bill on the load-bearing shape.

Every finding below is in the plan's *text around* that shape, not the shape
itself: one stale row that still prescribes the refuted `after_commit` write, one
registration sentence that silently disables half the listeners, and a
dev/prod-divergence section whose stated mechanism is backwards and whose
prescribed mitigation cannot fail.

## Findings

### HIGH 1 — The risk table still tells the implementer to write in `after_commit` [CODE-CONFIRMED]

`plan.md:266`, first row of "Risks carried into implementation":

> | `after_insert` cannot do async work | Collect in the listener, write in **`after_commit`**; slice 2 exists to prove this shape before anything depends on it |

This is the exact shape the plan's own "What was wrong, so nobody re-derives it"
table (line 88) declares broken, on the same page. Revision 2 rewrote the
mechanism section and left the risk table untouched.

I re-ran it rather than trusting round 1. Registering `after_commit` with the
plan's own fail-open handler, on file-backed SQLite:

```
[after_commit shape] rows_persisted=[]
  swallowed=["InvalidRequestError: This session is in 'committed' state;
              no further SQL can be emitted within this transaction."]
```

Zero rows, and the error is eaten by the `except (SQLAlchemyError, ValueError)`
the plan specifies. This matters more than a stale sentence normally would: slice
prompts get built from the risks table, and this row also claims "slice 2 exists
to prove this shape" — pointing the checkpoint at the broken shape. Delete the
row or rewrite it to say `after_flush_postexec`.

### HIGH 2 — "Bound to the `Session` class" silently disables the `after_insert` half [CODE-CONFIRMED]

`plan.md:123-125`:

> **Registration at import, not startup** — mirroring the existing
> `install_sqlite_parity_guards` pattern, and bound to the `Session` class rather
> than the async session factory, so it is active in every test fixture too.

The sentence covers both hooks, but `after_insert` is a **`MapperEvents`** event,
not a `SessionEvents` one. The dangerous part is that SQLAlchemy does not reject
it:

```
[SILENT-NO-OP] event.listen(Session, 'after_insert') ACCEPTED but fires?
               :: callback invocations=0
```

`event.listen(Session, "after_insert", cb)` returns cleanly and the callback
never runs. The reason is in `MapperEvents._accept_with`: `Session` is a `type`
that is not a `Mapper` subclass and not a mapped class, so it falls through to
`_MapperEventsHold(Session)` — the "this class will be mapped later" holding pen.
`Session` is never mapped, so the listener waits forever.

Combined with the fail-open recorder, an implementer who follows this sentence
literally gets: green `ruff`, green `mypy`, green `pytest`, zero log lines, and a
`user_milestones` table that never receives `signed_up`, `set_up_ai_agent`,
`set_up_human_play` or `joined_match`. That is the same silent-success failure
mode as both round-1 CRITICALs, reintroduced by the sentence written to fix them.

Fix: state the split explicitly — `after_insert` on `User`, `Agent` and `Player`
(or on `Mapper`); only `after_flush_postexec` on `Session`. Add a slice-2
assertion on `event.contains(User, "after_insert", ...)` for each model, not just
"listeners fire", so a mis-binding fails at registration rather than at the
dashboard.

### HIGH 3 — The dev/prod divergence is described backwards, and its mitigation cannot fail [CODE-CONFIRMED]

`plan.md:127-137` is the section slice 2's proof list is built on. Both halves
are wrong.

**The stated mechanism does not reproduce.** The plan says "A savepoint write
survives a session closed without committing on SQLite, but not on Postgres
(measured both in-memory and file-backed)." Against the *new* shape, on
file-backed SQLite:

```
[clean] T6 file-SQLite, session closed WITHOUT commit :: milestones=[] users=0
[clean] T6b explicit rollback of the caller's txn     :: []
```

Nothing survives. Not the milestone, not the caller's own user row.

**The real defeater is the fixture, and the plan's mitigation walks straight into
it.** `sqlite+aiosqlite:///:memory:` — what conftest's `engine`, `db` and
`reset_db` all build — resolves to **`StaticPool`**: one shared DBAPI connection
for every session on that engine. A "fresh session" therefore reads the writer's
*uncommitted* transaction:

```
[NON-DISCRIMINATING] in-memory: pool=StaticPool
                     seen=['set_up_ai_agent', 'signed_up']   # never committed
[discriminating]     file:      pool=AsyncAdaptedQueuePool  seen=[]
```

I wrote slice 2's must-prove literally, as a test using the repo's real
`reset_db` fixture, against a writer that flushes and never commits:

```python
async def test_plan_slice2_shape_passes_even_without_commit(reset_db):
    async with reset_db() as writer:
        await _mk(writer, 3)
        await writer.flush()                      # NO COMMIT — the prod-losing bug
        async with reset_db() as fresh:           # "a fresh session after commit"
            got = (await fresh.execute(select(UserMilestone.milestone))).scalars().all()
        assert list(got) == ["signed_up"]
```

It **passes**. The assertion the plan calls the guard against silent production
row-loss goes green against the missing commit it exists to catch. This is round
1's decisive finding — "slice 2's own must-prove list would have PASSED against
the broken shape" — surviving into revision 2 in a new costume.

Fix: slice 2's discriminating test must use a file-backed SQLite DB (a
`tmp_path` URL, `AsyncAdaptedQueuePool`) or a second independent engine. Say so
in the plan, because "a fresh session" reads as sufficient and is not. Replace
the divergence paragraph with what was actually measured, or the next reviewer
re-derives it.

### MEDIUM 4 — The collection is never cleared, so every later flush replays it [CODE-CONFIRMED]

The mechanism sketch (`plan.md:101-113`) shows `_record_sync` but never shows the
`after_flush_postexec` body, and nothing in the plan says the queue in
`session.info` is consumed. `session.info` lives for the whole Session and
survives both `commit()` and `rollback()`. Measured on a request doing one user
insert plus ten more flushes:

```
[WASTE] 66 insert attempts for 2 distinct milestones
```

Quadratic in flushes per request. The plan's own risk table says the opposite:

> Only the *first* genuine play writes; the unique constraint short-circuits the rest.

The unique constraint is not a short-circuit. Each replay is a full round trip
plus a `SAVEPOINT`/`ROLLBACK TO` pair plus DBAPI exception construction — on the
turn-submission hot path the same row flags. On Postgres it is strictly worse,
because the failed statement aborts the transaction until the savepoint rollback
un-aborts it.

Second effect: because the queue survives `rollback()`, a milestone can be
replayed against a parent that no longer exists. It is swallowed (see the credit
in Residual Risks), but on SQLite dev, rowid reuse after a rollback means the
replay can land on a *different* user.

Fix: `rows = session.info.pop(_KEY, None)` in the postexec hook, and put that one
line in the plan next to `_record_sync`.

### MEDIUM 5 — `seating.get_or_create_bots_user` is a third `User`-creation site the plan never lists [CODE-CONFIRMED]

`grep` finds exactly three `User(...)` constructions in `app/`:

- `app/routes/auth.py:41` — named (via `sync_google_user`, slice 5)
- `app/routes/dev_login.py:51` — named (slice 4)
- `app/engine/bots/seating.py:51` — **named nowhere in the plan**

Driving the real `get_or_create_bots_user` through the mechanism:

```
[RECORDS] house bots User creation records a milestone
          rows=[(1, 'signed_up')]  sub=platform:bots
```

The house account enters the signup count. Slice 4's "creation wiring" bullet
lists the predicate and the backfill but not this site, so on any DB where the
bots user is created after the backfill it carries `is_internal` at its `false`
server default — counted as a signup, and (it has no handle) shown in the AC18
stuck list as a user who never picked one. Its email `bots@agentludum.local` is
inside the default `INTERNAL_EMAIL_DOMAINS`, so the fix is only to route this
site through the same predicate — but it has to be named to be routed.

Related, same account: the mapping table (`plan.md:141-146`) lists `Agent` kind
`ai` and kind `human` and does not say what happens to kind `bot`. A natural
`if kind is AI … else set_up_human_play` attributes `set_up_human_play` to the
house account for every seated bot. State the exclusion.

### MEDIUM 6 — The mandated re-entrancy guard cannot be needed here, and inviting it risks silent write-loss [CODE-CONFIRMED / CODE-REFUTED]

`plan.md:159-162`: "postexec writes must be guarded against re-entrancy or they
hit `FlushError` after 100 flushes."

Refuted for the shape the plan chose. 150 flushes in one session with the
Core-insert recorder:

```
[PASS] T3 150 flushes in one session :: 300 milestones, postexec fired 300x
```

No error, because `conn.execute(insert(...))` never marks the session dirty, so
`_prepare_impl`'s flush loop never re-enters. The `FlushError` is real, but only
for a hook that adds *ORM objects unconditionally* — I reproduced it deliberately
in both variants:

```
[REPRODUCED] FlushError: Over 100 subsequent flushes have occurred within
             session.commit() ... after 101 hook calls   (after_flush)
[REPRODUCED] ... after 101 hook calls                    (after_flush_postexec)
```

Left as a hard requirement, an implementer adds a stateful suppression flag
(`session.info["_recording"] = True` or a module global). That flag is exactly
the kind of thing that stays set across nested flushes and drops legitimate
writes — trading an impossible failure for a plausible silent one. Rewrite as:
"cannot arise, because the recorder never touches the ORM unit of work; it would
arise if the recorder ever went back to `session.add`."

### MEDIUM 7 — Nothing in this feature has ever been run against Postgres [UNVERIFIED]

Round 1's measurements, revision 2's rewrite and everything above are aiosqlite.
Production is Postgres, and `railway.json` runs `alembic upgrade head` as
`preDeployCommand` on merge to `main` — the plan's own "this is NOT inert by
default" correction. The single behaviour that genuinely differs is the one the
mechanism leans on: on Postgres a failed statement aborts the whole transaction
until `ROLLBACK TO SAVEPOINT`, so `except IntegrityError: pass` is safe *only*
because `conn.begin_nested()` wraps it. That is the canonical pattern and I have
no reason to think it fails — but the plan flags a dev/prod divergence and then
assigns no Postgres coverage anywhere in slices 1-8, so the first Postgres
exercise of the recorder is production traffic. Slice 6 already requires a
dry-run against a production copy; the cheapest fix is to run the slice-2
recorder tests against that same copy while it exists.

### LOW 8 — The Core insert bypasses `install_sqlite_parity_guards`, so the stated reason for catching `ValueError` does not apply on that path [CODE-CONFIRMED]

`plan.md:115-116`: "`ValueError` is caught deliberately: `StringLengthExceeded`
is a `ValueError` and would otherwise escape the advisory catch."

True for the async wrapper — I confirmed it fires there and is swallowed
cleanly, with the caller's later commit still succeeding. Not true for
`_record_sync`. The parity guard is a `before_flush` ORM hook, and
`app/sqlite_parity.py`'s own docstring says so: Core statements bypass the flush
and are not checked. Measured, writing 64 characters into the `String(32)`
`milestone` column via `conn.execute(insert(...))` on file-backed SQLite:

```
[BYPASSED] declared=32 stored=64 -> Postgres would raise, SQLite stores it
```

Exposure today is nil (milestone values are constants, `source_match_id` mirrors
`Match.id` which is also `String(32)`). The cost is a stated invariant the code
does not have, on the one write path that reaches production unguarded — and the
next person to add a user-supplied string to the milestone row will believe the
net is there.

## Residual Risks

**Credit, measured, so nobody re-litigates these.** All of the following were run
and behaved correctly; they are the plan's genuine wins.

- Rows persist past the caller's commit and are visible from an independent
  connection on file-backed SQLite: listener path, async wrapper, and both mixed
  in one session.
- A duplicate raises `IntegrityError` inside the savepoint, is swallowed, and the
  caller's transaction stays fully usable — the caller's own rows (three agents)
  still commit. Same for the async wrapper.
- The listener write composes with the existing savepoint at
  `mcp_connection.py:267`. A nested `async record_milestone` *inside* that
  savepoint also works, including a duplicate inside the duplicate.
- An FK violation in the recorder (parent user absent) is swallowed and the
  caller continues to commit normally.
- `StringLengthExceeded` raised from the parity guard inside the async wrapper's
  savepoint is caught by the plan's handler and does **not** leave the session in
  `PendingRollbackError` — the caller's next commit succeeds.
- The listeners fire under conftest's `db` and `reset_db` fixtures and are
  registered under `client`, with no lifespan involved. Binding
  `after_flush_postexec` to `Session` genuinely does what the plan claims; it is
  only the `after_insert` half (HIGH 2) that does not.
- A `SELECT` inside `after_insert` — needed for the house-bots exclusion — does
  **not** trip autoflush re-entrancy, via either `Session.object_session(target)`
  or the `connection` argument. Either spelling is safe.
- The plan's stated `after_insert` limit holds: no Core bulk insert or
  `bulk_save_objects` of `User` / `Agent` / `Player` exists in `app/`,
  `mcp_server/`, `scripts/` or `migrations/`. `web_join.py:403`'s `add_all` is
  ORM and fires normally.

**Carried, not findings.**

- Registration idempotency is implied by "mirroring `install_sqlite_parity_guards`"
  but not stated. That pattern's `if not event.contains(...)` guard is the load-bearing
  half; without it a double import doubles every write attempt (harmless, but it
  doubles the hot-path cost in MEDIUM 4).
- Concurrency: two requests racing the same first milestone on Postgres resolve
  through the same `IntegrityError`-inside-savepoint path. Correct by
  construction, unexercised by any planned test.
- `reached_at` on SQLite stores naive datetimes even under
  `DateTime(timezone=True)`. Irrelevant to the mechanism, but AC16's read-time
  timezone derivation is built on this column, and dev/test comparisons will not
  behave like prod.
- Slices 3-8 all sit downstream of slice 2's checkpoint. Until HIGH 3 is fixed,
  that checkpoint can go green on an implementation that records nothing, which
  is precisely how revisions 1 and 2 each reached a review.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Risk table still prescribes the refuted after_commit write", "detail": "plan.md:266 tells the implementer to 'write in after_commit' and says slice 2 will prove that shape, which I re-ran and measured at 0 rows persisted with the error swallowed by the plan's own fail-open handler."}, {"severity": "HIGH", "title": "'Bound to the Session class' silently no-ops the after_insert listeners", "detail": "after_insert is a MapperEvents event; event.listen(Session, 'after_insert', cb) is accepted without error by SQLAlchemy 2.0.50 (it falls through to _MapperEventsHold) and fires zero times, so the four insert-driven milestones would never record and nothing would ever error."}, {"severity": "HIGH", "title": "Dev/prod divergence described backwards and its mitigation cannot fail", "detail": "On file-backed SQLite the new shape persists nothing when the session closes uncommitted (refuting the stated savepoint quirk), while the repo's in-memory fixtures use StaticPool so a 'fresh session' reads uncommitted rows - I wrote slice 2's must-prove literally against reset_db and it passes against a writer that never commits."}, {"severity": "MEDIUM", "title": "session.info collection is never cleared, replaying on every later flush", "detail": "Measured 66 insert attempts for 2 distinct milestones across 11 flushes, and the plan's claim that 'the unique constraint short-circuits the rest' is wrong - each replay is a full round trip plus a SAVEPOINT/ROLLBACK pair on the turn-submission hot path."}, {"severity": "MEDIUM", "title": "seating.get_or_create_bots_user is an unlisted third User-creation site", "detail": "Driving the real helper records signed_up for the house bots account, and app/engine/bots/seating.py:51 appears nowhere in slice 4's creation wiring, so the platform account enters the signup count and the AC18 stuck list unless is_internal is applied there."}, {"severity": "MEDIUM", "title": "Mandated re-entrancy guard cannot be needed and invites silent write-loss", "detail": "150 flushes with the Core-insert recorder produced 300 milestones and no error because the session is never made dirty; the FlushError only reproduces for a hook that adds ORM objects unconditionally, so requiring a guard pushes the implementer toward a suppression flag that can drop real writes."}, {"severity": "MEDIUM", "title": "No Postgres coverage anywhere in slices 1-8", "detail": "Every measurement in rounds 1-3 and in this review is aiosqlite, yet the load-bearing behaviour (a failed statement aborting a Postgres transaction until ROLLBACK TO SAVEPOINT) is Postgres-only and railway.json runs the migration as preDeployCommand, so production is the first Postgres exercise."}, {"severity": "LOW", "title": "Core insert bypasses install_sqlite_parity_guards, voiding the stated ValueError rationale", "detail": "The parity guard is a before_flush ORM hook that Core statements bypass by design, and a 64-character value written into the String(32) milestone column stored all 64 characters on SQLite where Postgres would raise."}]}
```
