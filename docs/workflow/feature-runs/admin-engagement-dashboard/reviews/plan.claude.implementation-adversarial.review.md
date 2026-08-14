---
reviewer: "claude"
lens: "implementation-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/plan.md"
artifact_sha256: "92e652d2a16a9f14388d71b8b9e0804b88eb18af7a7f733295eb61f3c8d61d15"
repo_root: "."
git_head_sha: "92d75f04fcf9d26cc10261576f4658cb187b56c3"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "12 findings (2 CRITICAL, 3 HIGH). The reviewer RAN the proposed mechanism against the repo's SQLAlchemy 2.0.50 + aiosqlite stack rather than reasoning about it, and both load-bearing shapes were broken AND failed silently. CRITICAL 1: after_commit cannot emit SQL (InvalidRequestError, 0 rows persisted, swallowed by the plan's own fail-open handler) - after_flush_postexec is the working point. CRITICAL 2: db.begin_nested() is a no-op inside a flush because _prepare_impl skips its flush loop when session._flushing, so the savepoint protected nothing and a duplicate would raise out of the caller's commit - working shape is session.connection() + conn.begin_nested() + Core insert(). Also accepted: sync listeners cannot call an async recorder (two entry points now); savepoint writes survive an uncommitted session on SQLite but not Postgres, so a missing commit passes every test and loses rows in prod (slice 2 now asserts from a fresh session after commit); the Player listener would have recorded joined_match for the house bots user; the real played_turn pair is agent_play.submit_action:269 and web_play.py:253; registration must be at import on the Session class, not app startup. All folded into plan revision 2."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/plan.claude.implementation-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan implementation-adversarial

## Findings

Every finding below was checked against this repo. I ran the plan's proposed
listener shapes as real code on the repo's own stack (SQLAlchemy 2.0.50 +
aiosqlite, an `async_sessionmaker` configured exactly like `app/db.py`) rather
than reasoning about the docs. Scratch scripts were throwaway and are deleted.

The headline: **the plan's stated milestone mechanism does not work, in two
independent ways, and both fail silently.**

---

### CRITICAL 1 — `after_commit` cannot write anything; the plan's own error handling hides that [CODE-CONFIRMED]

The plan names `after_commit` as the write point twice — in the listeners
section (`after_flush_postexec` / `after_commit`) and again in the risk table
("Collect in the listener, write in `after_commit`").

I ran it. `after_commit` raises immediately:

```
InvalidRequestError: This session is in 'committed' state;
no further SQL can be emitted within this transaction.
```

Rows written: **0**. Not "sometimes zero" — zero, on the first and every
attempt. Adding an inner `session.commit()` fails the same way.

This is worse than a plain bug because of how the plan's recorder handles
errors. `InvalidRequestError` is a subclass of `SQLAlchemyError`, so the
recorder's own `except SQLAlchemyError: logger.exception(...)  # fail-open`
swallows it. The result in production: `signed_up`, `set_up_a_way_to_play` and
`joined_match` are **never recorded at all**, the dashboard shows zeroes for the
top of the funnel, and the only evidence is a log line nobody reads. Slice 2
"proves the shape" only if its test asserts a row landed in a *separate*
session — asserting on the same session, or asserting "no exception reached the
caller", passes while recording nothing.

The plan is also internally ambiguous: the body says
`after_flush_postexec` / `after_commit` (an either/or with a slash), the risk
table says `after_commit`. An implementer resolves that coin-flip on their own,
and one face is total data loss.

**The working write point is `after_flush_postexec`.** Confirmed: collect in
`after_insert`, write in `after_flush_postexec`, row persists (verified by
re-reading from a fresh session). `before_commit` does *not* work either — it
fires before the flush, so the `after_insert` collection is still empty
(verified: `pending=[]`, 0 rows).

---

### CRITICAL 2 — `db.begin_nested()` is a no-op inside a flush, so a duplicate breaks the caller's transaction [CODE-CONFIRMED]

This is the exact failure the plan says the savepoint prevents, and the plan's
combination reintroduces it.

`record_milestone` uses `async with db.begin_nested(): db.add(...)`. That shape
works **only** because exiting the savepoint block calls the nested
transaction's `commit()`, which flushes the pending object *inside* the
savepoint. I verified that standalone: duplicate contained, caller commits fine.

But when the same recorder is called from a flush-time listener, SQLAlchemy
skips that flush. `SessionTransaction._prepare_impl` guards the flush loop with
`if not self.session._flushing:` — and inside `after_flush_postexec` the session
*is* flushing. So the savepoint opens, releases empty, and the milestone object
stays pending. It is then inserted by the **outer** flush loop, outside any
savepoint.

Measured result, savepoint recorder called from `after_flush_postexec` with a
duplicate present:

```
[postexec] wrote 1/signed_up        <- listener reports success
IntegrityError: UNIQUE constraint failed: user_milestones.user_id, ...
   raised out of the caller's db.commit()
```

The listener prints success. The caller's commit dies. That is a direct
violation of spec AC22 ("advisory: a failure logs and never breaks the request
that triggered it"), and it fires on the *second* signup-like event for any user
whose milestone already exists — a routine case, not an edge case.

**The shape that actually works** (verified, both with and without a duplicate,
and with further caller work after the containment):

```python
def _write_pending(session, flush_context):
    pending = session.info.pop("pending_milestones", [])
    if not pending:
        return
    conn = session.connection()              # sync Connection, inside the flush
    for row in pending:
        try:
            with conn.begin_nested():        # REAL savepoint on the connection
                conn.execute(insert(UserMilestone).values(**row))
        except IntegrityError:
            pass                             # already recorded
```

The difference is that `conn.execute()` runs the INSERT *inside* the savepoint,
so the rollback actually contains it. Results: no duplicate → 1 milestone,
caller commit OK. Duplicate → "contained by savepoint", caller commit OK. A
contained duplicate followed by more caller work → 2 milestones, 2 users, commit
OK. Core `insert()` also sidesteps the ORM entirely, which matters for the next
finding.

---

### HIGH 3 — the recorder is `async def`, but every viable hook is a sync callback [CODE-CONFIRMED]

`record_milestone` is declared `async def ... (db, user_id, milestone, ...)` and
the plan says the listener "collects into `session.info` and a matching
`after_flush_postexec` / `after_commit` hook performs the writes". All three
SQLAlchemy hooks — `after_insert`, `after_flush_postexec`, `after_commit` — are
**synchronous** callbacks on the sync `Session`. You cannot `await` in them.
`Session.object_session(target)` inside `after_insert` returns the sync
`Session`, not the `AsyncSession` (verified: it returns a live sync session and
`target.id` is already populated).

So the plan's single recorder cannot serve both paths as written. The build
needs either two entry points (a sync core the listener calls plus a thin
`async def` wrapper for the explicit call sites) or an explicit statement that
the listener path uses a different function. The plan implies one function and
budgets one file for it. An implementer hitting this mid-slice-2 will improvise,
and the most natural improvisation — `asyncio.create_task(record_milestone(...))`
— writes on a session that is about to close.

---

### HIGH 4 — savepoint writes persist on SQLite without a commit and vanish on Postgres; the whole test suite is blind to it [CODE-CONFIRMED]

I tested "recorder opens a savepoint, writes, session closes without the caller
ever committing":

| DB | savepoint recorder | plain `add` + `flush` |
|---|---|---|
| in-memory SQLite (whole test suite) | **1 row survives** | 0 rows |
| file-backed SQLite | **1 row survives** | 0 rows |

pysqlite/aiosqlite does not emit `BEGIN` for the outer transaction, so
`SAVEPOINT … RELEASE` commits at the driver level. On Postgres the savepoint sits
inside a real transaction, and `AsyncSession.close()` rolls it back. So a call
site that forgets to commit **passes every test and silently loses the row in
production**, and no SQLite test can ever catch it.

This is not hypothetical — it lands on `ai_connected`, which the plan puts at
`connection_activity.mark_seen`. That function does its own
`await db.execute(update(...))` then `await db.commit()` (`connection_activity.py`,
around line 128), and it is called from `require_connection`
(`app/deps.py:211`) — a FastAPI dependency that runs on read-only agent
endpoints too. If the recorder runs after `mark_seen`'s commit, the milestone
lands in a fresh auto-begun transaction that many of those request handlers
never commit. Green locally, empty in prod.

Two things the plan must state: the recorder call goes **before** `mark_seen`'s
`await db.commit()` (also required because `mark_seen` reads
`first = bot.first_connected_at is None` before the UPDATE — that transition is
only knowable there), and slice 2's proof list needs a test that asserts the row
from a **new session after the caller commits**, not from the writing session.

---

### HIGH 5 — the `Player` listener records `joined_match` for the house bots user [CODE-CONFIRMED]

The plan's mapping table excludes bots only for `Agent` ("Bots (`kind='bot'`)
record nothing"). The `Player` row has no such exclusion.

But bot seats *are* `Player` rows, owned by a real `users` row:
`app/engine/bots/seating.py:151` creates `Player(match_id=…, user_id=bots_user.id,
agent_id=agent.id, …)` for every bot in every match, and
`get_or_create_bots_user` (same file, line 45) creates the single house user
`bots@agentludum.local`. That user is itself created with `db.add(user)` +
`flush()`, so the `User` listener records `signed_up` for it too.

Net effect: with an unfiltered `after_insert` on `Player`, the house bots user
appears in `signed_up` and `joined_match`, and only stops appearing once slice 4
sets `is_internal` **and** the read models filter on it. Slice 2 and slice 3 ship
before slice 4, so between those checkpoints the numbers are wrong, and if the
read-model filter is ever missed the number stays wrong. The fix is cheap — skip
when the joining agent's `kind == BOT`, same rule as the Agent mapping — but the
plan has to say it, because the two rules currently disagree.

---

### MEDIUM 6 — "registered once at app startup" cannot work for the session-level hook, and misses the tests [CODE-CONFIRMED]

I checked the registration surface. Only one target accepts these events:

| Target | Result |
|---|---|
| `event.listen(Session, "before_flush", fn)` (sync class) | works, and fires for **every** sessionmaker in the process |
| `event.listen(SessionLocal, …)` (`async_sessionmaker`) | `InvalidRequestError: No such event` |
| `event.listen(AsyncSession, …)` | `InvalidRequestError: No such event` |

So the session-level hook must go on the sync `Session` class. "Registered once
at app startup" is then wrong in two ways. First, `app/main.py`'s lifespan is
never entered by most tests — `tests/conftest.py` builds `async_sessionmaker`
directly for the `engine`/`session_factory`/`db` fixtures and rebinds
`app.db.SessionLocal` for `reset_db`; none of that boots the app. Second, the
repo already has the right precedent: `app/db.py` calls
`install_sqlite_parity_guards()` at **import** time, which does
`event.listen(Session, "before_flush", …)` guarded by `event.contains(...)` for
idempotency. The plan should copy that pattern verbatim and say so, or slice 2's
listeners will be absent in exactly the tests meant to prove them.

The mapper-level `after_insert` on `User`/`Agent`/`Player` is fine wherever it is
registered — mapper events are global — but it must be imported, which is the
same problem in a different coat.

---

### MEDIUM 7 — an unguarded `after_flush_postexec` write hits `FlushError` after 100 flushes [CODE-CONFIRMED]

`Session.commit()` re-flushes until the session is clean, up to 100 times.
Adding rows from `after_flush_postexec` therefore triggers another flush, which
fires the hook again. With a handler that adds unconditionally:

```
FlushError: Over 100 subsequent flushes have occurred within session.commit()
 - is an after_flush() hook creating new objects?
postexec fired 100 times
```

The plan's "collect then drain" shape avoids this *if* the drain uses `pop` (the
second pass sees an empty list — verified: fires twice, writes once). That is
load-bearing behaviour the plan never states, and the obvious-looking variant
(read the list, write, clear later) hangs the request for 100 flush cycles then
500s. Worth one sentence in the plan and one test in slice 2.

Related and also unstated: `after_flush_postexec` fires on **autoflush** too
(verified — a bare `select()` after an `add` fires it). So the milestone is
written the moment anything reads, not when the caller decides.

---

### MEDIUM 8 — slice 7's dependency list is wrong and its proof list drops an acceptance criterion [CODE-CONFIRMED]

The build table says slice 7 (read models) depends on slice **1** only. But spec
AC9 is "Bots and internal users excluded everywhere, via the stored flag", and
that flag is `users.is_internal`, built in slice **4**. Slice 7's "must prove"
list — distinct users, share suppression, return timezone, smoke-test exclusion,
empty DB — never mentions internal or bot exclusion at all.

Given finding 5 (the house bots user lands in the funnel), this is the criterion
most likely to be silently skipped: nothing in slice 7 forces it, and by slice 8
the page renders plausible-looking numbers. Slice 7 should read "Depends on 1, 4"
and carry "internal and bot users excluded" in its proof list.

---

### MEDIUM 9 — slice 2 turns global listeners on for the entire existing suite, with no regression clause [CODE-CONFIRMED]

`tests/factories.make_user` does `db.add(user)` then `await db.flush()`, and 90
test files create users. The moment slice 2 registers `after_insert` on `User`,
`Agent` and `Player`, every one of those flushes starts writing milestone rows,
inside those tests' transactions.

That is a large blast radius for a checkpoint whose "must prove" list is only
about the new code. The listener now runs in tests that assert row counts, in
`scripts/` tools, and in any session whose schema was not built by
`Base.metadata.create_all`. Combined with CRITICAL 2, a duplicate anywhere in
that surface turns into a failed caller commit in an unrelated test, and the
diff checkpoint will read as "unrelated flakiness".

Slice 2 needs an explicit line: the full suite stays green with the listeners
active, and that is part of what slice 2 proves.

---

### MEDIUM 10 — the two `played_turn` hooks are never named, and the two names the plan *does* give are the wrong places [CODE-CONFIRMED]

The plan says: "two request-level hooks — `record_player_action` and
`record_submission` are each shared by genuine and non-genuine paths, so the
hook sits at the request level where the caller is known." It correctly rules
those two out and then never says what to use instead. An implementer reading
only the plan has two function names and an instruction not to use them.

The viable pair, from the call graph:

- **AI play** — `app/engine/agent_play.py::submit_action` (the
  `module.record_submission` call is at line 269). This is the single choke point
  for both AI entry paths: HTTP at `app/routes/agent_api.py:77` and MCP at
  `mcp_server/mcp_tools.py:379` both call it. It already carries
  `is_connector_fallback`, so slice 3's "connector-fallback records nothing" is
  testable right there. It commits at the end of the function, so the recorder
  call must go before that commit.
- **Human play** — the act handler in `app/routes/web_play.py` (the
  `record_player_action` call is at line 253, with `await db.commit()` just
  after). This is what makes AC4 pass.

Both are viable. Neither is named. The shared helpers stay clean because the
non-genuine paths — `app/engine/bots/service.py:124` (bots and autopilot) and
`app/engine/turn_drivers.py:136,142` (defaulted) — go through the same helpers
and never through these two functions.

---

### MEDIUM 11 — the plan never says whether a milestone survives the caller's rollback [CODE-CONFIRMED]

The architecture diagram calls `user_milestones` "durable, append-only". With a
flush-time listener that is only half true, and the plan does not pick a side:

- Caller flushes then rolls back → **0 milestones, 0 users** (verified). The
  milestone dies with the caller's work.
- Caller autoflushes mid-request → milestone already written, before the caller
  has decided anything (verified).

Both may be the behaviour you want. But "write-once at event time, durable" and
"rolls back with the transaction that caused it" are different contracts, and
slice 2's proof list tests neither. Pick one and write a test for it, because
this is the property the backfill (slice 6) and the read models (slice 7) both
assume without saying.

---

### LOW 12 — the repo's own SQLite parity guard raises an exception class the recorder does not catch [CODE-CONFIRMED]

`app/sqlite_parity.py` registers a `before_flush` guard that raises
`StringLengthExceeded(ValueError)` when a string exceeds its column length. That
is **not** a `SQLAlchemyError`, so it escapes `record_milestone`'s
`except SQLAlchemyError` and breaks the caller — the opposite of advisory. The
values at risk are small (`agent_kind` is `ai`/`human` in `String(16)`,
`source_match_id` holds ids like `M_1271` in `String(32)`), so the practical
risk is low today.

Second half of the same note: that guard explicitly skips `TypeDecorator`
columns, and the plan stores `milestone` as a `FlexibleEnumType`. So an
over-length milestone value is unchecked on SQLite and would only fail on
Postgres. Keep the milestone names short and the point is moot, but the safety
net the repo thinks it has does not cover this column.

---

## Residual Risks

**Verified sound, so implementers should not waste time re-deriving them.**
`after_insert` gives a populated `target.id` and a live `Session.object_session`,
so collecting `user_id` in the listener works. `after_insert` does **not** fire
for Core `insert()` — the plan's stated limit is exactly right, and slice 2's
assertion test is worth keeping. Nested savepoints compose: a recorder savepoint
inside the existing outer savepoint at `app/engine/mcp_connection.py:267` contains
a duplicate cleanly and the outer block still commits, so the four
`first_connected_at` branches (lines 145/174/206/222 — all four line numbers
verified accurate) are safe hook sites. `railway.json` really does set
`"preDeployCommand": ["alembic upgrade head"]` and `app/main.py:71` skips the
boot-time migration on Railway, so the plan's backfill reasoning holds. Slice 8's
anchors are real: `colspan="7"` at `app/templates/admin/users_list.html:41`,
`/admin/users/{user_id}` at `app/routes/admin_web.py:213`, and `AdminAction` is a
`FlexibleEnumType` (`String(16)`), so two new values need no DDL on either
dialect.

**Not resolvable here.** All transaction behaviour above was measured on SQLite
and aiosqlite, because that is what this machine has. Postgres semantics for
savepoints and for close-without-commit are standard and well documented, but the
specific divergence in finding 4 was only *observed* on SQLite; the Postgres half
is inference. If a Postgres URL is available at implementation time, run the same
close-without-commit probe against it before trusting any test that asserts a
milestone landed.

**Left standing after this review.** The plan gives no measurement for the
turn-path cost it promises to measure in slice 3 — no threshold, no baseline, so
"measure" cannot fail. Slice 6's backfill has no dry-run mode, which the repo's
own data-critical practice asks for on anything that rewrites production rows.
And the plan never states what happens to milestones already recorded during
slices 2–3 for the house bots user once slice 4 lands — the `is_internal`
backfill fixes the flag, but nothing cleans the rows, so whether the read models
filter by flag or by row is a decision still owed.

**Sequencing.** Slice 2 is genuinely provable before slice 3 exists — its proof
list needs only slices 1 and 2 — but only if the mechanism is fixed first and the
proof list is tightened to (a) assert from a fresh session after commit, (b) keep
the whole existing suite green with listeners on. As written, slice 2 would sail
through its checkpoint while recording nothing, and slices 3 through 8 would all
be built on top of that.

```json
{"reviewed": true, "findings": [{"severity": "CRITICAL", "title": "after_commit cannot emit SQL, so the plan's stated write point records nothing", "detail": "Ran on this repo's SQLAlchemy 2.0.50: writing from after_commit raises InvalidRequestError ('session is in committed state') and persists 0 rows, and because that is a SQLAlchemyError the recorder's own fail-open handler swallows it, so signed_up, set_up_a_way_to_play and joined_match would silently never be recorded; after_flush_postexec is the working write point and before_commit is also refuted (fires before the collection exists)."}, {"severity": "CRITICAL", "title": "db.begin_nested() is a no-op inside a flush, so a duplicate breaks the caller's commit", "detail": "Verified: SessionTransaction._prepare_impl skips its flush loop when session._flushing is true, so the ORM object added inside the savepoint is inserted by the outer flush instead and a duplicate raises IntegrityError out of the caller's db.commit(), violating AC22; the shape that actually contains it is session.connection() + conn.begin_nested() + a Core insert()."}, {"severity": "HIGH", "title": "The async recorder cannot be called from any of the listener hooks", "detail": "after_insert, after_flush_postexec and after_commit are all synchronous callbacks on the sync Session (object_session returns the sync session), so `async def record_milestone` cannot be awaited from the listener path and the plan's single shared recorder needs to be split into a sync core plus an async wrapper."}, {"severity": "HIGH", "title": "Savepoint writes persist on SQLite without a commit but roll back on Postgres", "detail": "Measured on both in-memory and file-backed SQLite: a savepoint write survives a session closed without commit (1 row) while a plain flush does not (0 rows), so a call site that forgets to commit passes every test and silently loses the row in prod - which directly threatens ai_connected, since mark_seen commits itself and is called from require_connection on read-only endpoints that never commit again."}, {"severity": "HIGH", "title": "The Player after_insert listener records joined_match for the house bots user", "detail": "bots/seating.py:151 creates Player(user_id=bots_user.id) for every bot seat and get_or_create_bots_user creates a real users row (bots@agentludum.local) that the User listener also records signed_up for, but the plan's exclusion rule covers only bot Agents, so the funnel is polluted from slice 2 until slice 4's is_internal flag plus an unstated read-model filter."}, {"severity": "MEDIUM", "title": "Session-level listeners cannot be registered at app startup or on the session factory", "detail": "Verified that event.listen rejects both async_sessionmaker and AsyncSession ('No such event') so only the sync Session class works, and registering in the FastAPI lifespan would miss every test using the db/reset_db fixtures since conftest builds its own sessionmakers without booting the app - the repo's own install_sqlite_parity_guards() import-time pattern in app/db.py is the shape to copy."}, {"severity": "MEDIUM", "title": "Unguarded writes from after_flush_postexec hit FlushError after 100 flushes", "detail": "Measured: a handler that adds rows on every call triggers 'FlushError: Over 100 subsequent flushes have occurred within session.commit()', so the plan's collect-then-drain must use pop() to leave the second pass empty - load-bearing behaviour the plan never states - and the hook also fires on plain autoflush, not only on commit."}, {"severity": "MEDIUM", "title": "Slice 7 depends on slice 4, and its proof list omits internal/bot exclusion", "detail": "The build table lists slice 7 (read models) as depending on slice 1 only, but spec AC9 requires bots and internal users excluded via users.is_internal which is built in slice 4, and slice 7's must-prove list never mentions the exclusion at all - the criterion most likely to be silently skipped given the bots-user pollution."}, {"severity": "MEDIUM", "title": "Slice 2 switches global listeners on for the whole existing suite with no regression clause", "detail": "tests/factories.make_user flushes a User immediately and 90 test files create users, so slice 2's mapper events start firing across the entire suite; combined with the savepoint defect a duplicate anywhere turns into a failed commit in an unrelated test, and slice 2's must-prove list should explicitly include 'full suite green with listeners active'."}, {"severity": "MEDIUM", "title": "The two played_turn hook points are never named, only the wrong ones are", "detail": "The plan rules out record_player_action and record_submission without saying what replaces them; the viable pair is app/engine/agent_play.py::submit_action (line 269, the single choke point shared by agent_api.py:77 and mcp_tools.py:379, and already carrying is_connector_fallback) and the act handler in app/routes/web_play.py (line 253), with both calls needing to sit before their function's db.commit()."}, {"severity": "MEDIUM", "title": "The plan never states whether a milestone survives the caller's rollback", "detail": "Measured: a milestone written from a flush-time listener is discarded when the caller rolls back (0 rows) and is written on a mere autoflush before the caller decides anything, which contradicts the architecture diagram's 'durable, append-only' framing - slice 2 tests neither behaviour and the backfill and read models both assume one of them."}, {"severity": "LOW", "title": "The repo's SQLite parity guard raises an exception the recorder does not catch", "detail": "app/sqlite_parity.py raises StringLengthExceeded (a ValueError, not a SQLAlchemyError) from before_flush so it would escape record_milestone's advisory catch and break the caller, and that same guard skips TypeDecorator columns so an over-length FlexibleEnumType milestone value is unchecked on SQLite and would only fail on Postgres."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: 12 findings (2 CRITICAL, 3 HIGH). The reviewer RAN the proposed mechanism against the repo's SQLAlchemy 2.0.50 + aiosqlite stack rather than reasoning about it, and both load-bearing shapes were broken AND failed silently. CRITICAL 1: after_commit cannot emit SQL (InvalidRequestError, 0 rows persisted, swallowed by the plan's own fail-open handler) - after_flush_postexec is the working point. CRITICAL 2: db.begin_nested() is a no-op inside a flush because _prepare_impl skips its flush loop when session._flushing, so the savepoint protected nothing and a duplicate would raise out of the caller's commit - working shape is session.connection() + conn.begin_nested() + Core insert(). Also accepted: sync listeners cannot call an async recorder (two entry points now); savepoint writes survive an uncommitted session on SQLite but not Postgres, so a missing commit passes every test and loses rows in prod (slice 2 now asserts from a fresh session after commit); the Player listener would have recorded joined_match for the house bots user; the real played_turn pair is agent_play.submit_action:269 and web_play.py:253; registration must be at import on the Session class, not app startup. All folded into plan revision 2.
