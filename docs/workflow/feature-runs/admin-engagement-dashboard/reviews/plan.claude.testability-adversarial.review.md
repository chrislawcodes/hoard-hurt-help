---
reviewer: "claude"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/plan.md"
artifact_sha256: "a84bb8248bc0f8aacf720f66a50f03b8a4d80f6b09dcdb4e5a2cdf2e22ed66dc"
repo_root: "."
git_head_sha: "3c0d43fb6ad25504dd127140636d28ddf4d2e50f"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Round 2. 15 findings (4 HIGH), two verified by execution. Decisive and independently corroborated by the implementation lens: revision 2's 'assert from a fresh session after commit' CANNOT FAIL, because app/db.py's in-memory engine uses StaticPool so every fresh session is the same DBAPI connection - correct and broken implementations return identical answers, and CI has no Postgres service. Worse than no mitigation because it manufactures confidence. Replaced with three graded options. Also accepted: coverage re-verified from scratch and still false for a DIFFERENT seven ACs - four partials from round 1 untouched plus three newly exposed (AC4's returned half, AC10's three-creation-sites, AC20's reversibility for the slice 4/5/6 migrations); this is the fourth consecutive round the coverage claim has been false and is recorded as a drafting blind spot. AC14 was unwritable as specified - no column distinguished captured-and-direct from never-captured, and D9's precedence rule PRODUCES the bug AC14 forbids; fixed by making first_source_channel three-valued. Credit recorded: six of the eight mapping rows do discriminate, and after_flush_postexec verified working independently."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/plan.claude.testability-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

*Lens: testability. Round-1 findings are not repeated. Where a claim could be run, I ran it
against this worktree's stack (SQLAlchemy 2.0.50 + aiosqlite, the same combination
`tests/conftest.py` builds). Findings are marked [CODE-CONFIRMED], [CODE-REFUTED] or
[UNVERIFIED].*

---

### HIGH 1 — "Assertions read from a fresh session after commit" does not catch the divergence it was added for, and no SQLite-only test can [CODE-CONFIRMED, executed]

This is the round-1 fix the plan leans on hardest. It does not work.

Two facts, both measured:

**(a) A "fresh session" in this suite is not a fresh connection.** `app/db.py:make_engine`
passes no `poolclass`, so SQLAlchemy's aiosqlite dialect picks the default for an in-memory
URL. I printed it:

```
pool class: StaticPool
```

StaticPool holds exactly one DBAPI connection for the engine's lifetime. Every session the
`db` / `reset_db` / `session_factory` fixtures hand out is the *same* connection. There is no
isolation boundary for the assertion to cross.

**(b) With the plan's exact recorder shape, the correct and the broken implementation return
the same answer.** I built the plan's mechanism verbatim — `after_insert` collects into
`session.info`, `after_flush_postexec` does `conn = session.connection()` then
`with conn.begin_nested(): conn.execute(insert(Milestone), row)` — and ran both paths:

```
pool class: StaticPool
A (caller commits)   -> fresh session sees: 1   (want 1)
B (no commit)        -> fresh session sees: 1   rows: [1]
C parents visible    -> 1
```

Case B is the production-losing bug: the caller flushed and closed without committing. The
*parent* row was correctly rolled back (line C shows only case A's parent survives) — and the
milestone row survived anyway. So the slice-2 assertion reads "row present" whether the
implementation commits or not. Its discriminating power against a missing commit is zero.

To be fair to the plan: the assertion is not worthless. It *does* catch a different break —
a write that lands in the writing session's identity map and never reaches the database at
all. That is a real failure and worth asserting. It is simply not the failure the plan's own
"dev/prod divergence that must be tested for" section names, and the plan states the opposite:
*"otherwise the checkpoint passes against a broken implementation."* It still does.

**Can any test catch it?** Not as the suite is built. `.github/workflows/ci.yml` runs
`uv run pytest -q` and nothing else — no `services:` block, no Postgres anywhere in the
workflow, and no test in `tests/` opens a Postgres connection. Inverting the assertion does
not help either: on SQLite the row survives a rollback, so "assert the milestone is gone after
a rollback" fails for the *correct* implementation too.

What would actually work, cheapest first:

1. **Make the commit structural, not behavioural.** For each explicit call site, assert
   ordering rather than durability: that `record_milestone` runs *before* the commit the
   caller already performs (`connection_activity.mark_seen:127` does
   `await db.execute(update(...))` then `await db.commit()`). A spy session that records the
   statement/commit sequence answers this on SQLite, deterministically, in a fast-lane unit
   test. This is the one I would take.
2. **A parity guard in the repo's own idiom.** `app/sqlite_parity.py` already exists to
   "make SQLite reject the same writes Postgres rejects", installed at import from `app/db.py`.
   Add a test-only session hook that fails if `user_milestones` rows were written in a
   transaction that closes without committing. Same pattern, same file neighbourhood, and it
   turns an invisible divergence into a red test.
3. **A Postgres-backed test.** Correct but not free: a CI service container, a second engine
   fixture, and a schema build path that is not `Base.metadata.create_all` against SQLite. If
   this is the answer, it is a slice of its own, not a line in slice 2.

Whatever is chosen, slice 2's must-prove should stop claiming the fresh-session read closes
this hole.

---

### HIGH 2 — The Risks table still prescribes the `after_commit` shape the plan itself refutes [CODE-CONFIRMED, executed]

Plan line 266, first row of "Risks carried into implementation":

> `after_insert` cannot do async work | **Collect in the listener, write in `after_commit`**;
> slice 2 exists to prove this shape before anything depends on it

The mechanism section 130 lines above says this shape persists zero rows and calls it
refuted. Revision 2 rewrote the mechanism and left the mitigation column pointing at the
broken shape. I re-ran it to be sure it is still broken:

```
=== Risks-table shape: collect in after_insert, WRITE IN after_commit ===
   after_commit hook RAISED (swallowed as advisory): InvalidRequestError
       This session is in 'committed' state; no further SQL can be emitted...
   caller commit reported: OK
   milestones visible in a NEW session: 0   (want 1)
```

Why this is a testability finding and not a typo: the mitigation column is the part a task
generator or an implementer scans for "what do I do about this risk". It also says *"slice 2
exists to prove this shape"* — so the one slice whose whole job is to settle the mechanism is
pointed at the shape that fails silently. Delete the row or rewrite it to
`after_flush_postexec`.

---

### HIGH 3 — Re-verified from scratch: the AC-to-test coverage claim is still false, for a different seven criteria [CODE-CONFIRMED]

The spec still says (line 411) *"Every criterion has a matching test in the plan below."*
I mapped all 25 criteria (0, 1, 1a, 1b, 2–22) against the slice must-prove column, the seven
highest-value tests, and the new assignment table. Revision 2 closed the seven criteria that
had *no* test at all. It did not touch the four **partials** round 1 listed, and three more
gaps are newly visible now that the design has changed.

| AC | Half that has a named test | Half that does not |
|---|---|---|
| **1** | 401 / 403 | "and is in the Platform admin submenu" — slice 8 lists the nav files as *build* items, no assertion |
| **4** | `set_up_*`, `joined_match`, `played_turn` (slice 3) | **`returned`** — AC4 names it explicitly; `returned` is now read-time derived (slice 7), and slice 7 only proves "return detection in the window timezone" (that is AC16). The decisive human path is still half-unproven |
| **10** | shared predicate; backfill/creation agreement | **"set at all three in-app creation sites"** — slice 4 proves stability and agreement, never that `auth.py`, `bots/seating.py:51` and `dev_login.py:51` each set the flag. The spec's own test plan lists "Bots-user and dev-login users created internal"; the plan dropped it |
| **13** | "MCP records channel" | "a real `?utm_source=mcp` does not collide" |
| **16** | "return detection in the window timezone" | "defaults to the browser's timezone, not UTC" |
| **20** | slice 1's schema migration round-trips | Slices 4, 5 and 6 each add a migration (is_internal backfill, source columns, milestone backfill). Only slice 1's is proved `upgrade`/`downgrade` clean. AC20 says *additive **and reversible*** — a backfill's `downgrade` is exactly where that is non-obvious |
| **22** | "a failing milestone write leaves the caller's transaction usable" | first-touch capture's half — AC22 covers both writers; slice 5's must-prove has no advisory-failure item |

AC4 is the one to fix first. Round 2 of the spec review called the human path the decisive
finding, and the plan's own highest-value test #1 is about it — and the `returned` half of that
same criterion still has no owner.

---

### HIGH 4 — Two of the eight new assignments name a test that cannot be written from this plan [CODE-CONFIRMED]

The mapping table is real in the sense that every row points at a slice. Two rows point at
work the plan gives no way to do.

**AC14 — `"unknown"` vs `"direct"`.** The assigned test is *"a user with no capture and a user
with a genuine direct visit render differently"*. Nothing in the plan or the spec says which
column distinguishes those two users. The data model lists `first_utm_source`,
`first_utm_medium`, `first_utm_campaign`, `first_referrer_host`, `first_landing_path`,
`first_source_channel` — all nullable, none designated as the "we captured this visitor"
sentinel. For a genuine direct visit, `utm_source` and `referrer_host` are both NULL, which is
byte-identical to never having captured anything. D9's precedence rule
(`utm_source` → `first_referrer_host` → `"direct"`) *produces the bug it forbids*: run it on a
never-captured user and you get `"direct"`. The only viable sentinel is `first_landing_path`
(the one field a direct visit always sets), and the plan never says so. As written the test is
unwritable, and the implementation it is meant to discriminate against is the one the
precedence rule literally describes.

**AC19 — both explanatory notes.** Spec D4 requires a **dated** line: *"Milestones before
&lt;deploy date&gt; are reconstructed..."*. The plan's complete config-key list is
`FIRST_TOUCH_CAPTURE_ENABLED` and `INTERNAL_EMAIL_DOMAINS`. There is no deploy-date key, no
migration-stamp read, no other source. So the assigned test can only assert a substring that
does not include the date, and the implementer has nowhere to get the date from.

Both fixes are one line each in the plan: name the sentinel column; name where the deploy date
comes from (a config key, or the `reached_at` of the oldest backfilled row).

---

### MEDIUM 5 — AC1b's n=19 / n=21 pair steps over the boundary the rule turns on [CODE-CONFIRMED]

Spec AC1b: shares are *"suppressed below 20 users"*. So n=19 suppressed, **n=20 shown**,
n=21 shown. The assigned test renders at 19 and 21 — it never touches 20.

The most likely implementation bug here is the off-by-one: `if n <= 20: suppress`. That
implementation passes both assigned cases (19 suppressed ✓, 21 shown ✓) and is wrong at
exactly the value the criterion names. A test that cannot fail against the most probable
defect is the false-confidence case this review round is looking for. Change the pair to
**n=19 and n=20**.

---

### MEDIUM 6 — AC1a's assignment is ambiguous between two different tests, and its literal text is the one less likely to be built [CODE-CONFIRMED]

Spec AC1a: *"Each of the three summary numbers **states** its window, its population, and that
it excludes internal users."* That is a **labelling** requirement about rendered text.

The assignment reads: *"one test per number asserting window, population, internal filtering"*
— which reads much more naturally as a **computation** requirement (the number is computed
over the right window, the right population, with internal users filtered out).

Whichever the implementer picks, the other half goes untested:

- Build the label check and a page that shows a wrong number under a correct caption passes.
- Build the computation check and a page whose captions never mention the window or the
  exclusion passes — which is the literal criterion.

Second problem: the numbers are computed in slice 7 (read models) and the criterion is
assigned to slice 8 (page). A computation assertion is far cheaper and sharper at the read
model. Split it: computation in 7, caption text in 8, and say which is which.

---

### MEDIUM 7 — AC9 is a quantifier over four surfaces and is assigned as one bullet [CODE-CONFIRMED]

Spec AC9: *"Bots and internal users excluded **everywhere**, via the stored flag."* Slice 7's
must-prove carries it as a single item, *"internal users excluded (AC9)"*, in a list that
covers three different read models.

Slice 7 alone builds milestone counts, `signup_sources` **and** the stuck list; slice 8 adds
the three summary numbers. One test that seeds an internal user and checks the milestone
counts passes while `signup_sources` and the stuck list happily list them. That is not a
hypothetical: D10 measured over 500 of 646 player rows as internal, and the stuck list — users
who have not progressed — is precisely where the harness accounts will surface.

Make it one assertion per surface, or restate the item as "each of the three read models plus
the three summary numbers excludes internal users".

---

### MEDIUM 8 — Revision 2's corrections landed in the prose sections but not in the must-prove table, which is what the checkpoint gates on [CODE-CONFIRMED]

The plan now contradicts itself in two places, and in both the stale half is the operative one.

| Prose section (corrected) | Must-prove table (unchanged) |
|---|---|
| Fixtures: connector-fallback rows are *"byte-identical"* to missed deadlines; *"a test claiming to tell them apart would be vacuous"* | Slice 3: *"autopilot, defaulted, **connector-fallback** and null-timestamp rows record nothing"* |
| Two test-harness gaps: *"'backfill and creation rule agree' **cannot be a single test**"*; slice 4 asserts the predicate, slice 6 asserts the migration | Slice 4: *"backfill and creation rule **agree on one fixture set**"* |

The must-prove column is what a checkpoint reads and what task generation expands. Leaving the
refuted wording there means slice 3 ships a vacuous test and slice 4 ships an impossible one,
each with a correction sitting 60 lines away that nothing points to.

---

### MEDIUM 9 — The bot-seat exclusion is built in slice 2 and proved in slice 3 [CODE-CONFIRMED]

Slice 2 is "Recorder + listeners". The house-bots-user exclusion lives on the `Player`
listener — the Mapping table puts it there. But the proof, *"bot seats record no
`joined_match`"*, is assigned to slice 3, "Explicit recorders (handle, connect, play)", which
has nothing to do with listeners.

So slice 2's checkpoint goes green with a Player listener that records `joined_match` for
every bot seat. Slice 2's must-prove is satisfied by exactly that implementation: *"listeners
fire for all three models"* is true, loudly. And `app/engine/bots/seating.py:151` seats bots in
a loop with `db.add(player); await db.flush()` per seat, so the pollution is per-bot,
per-match, immediately. Move the assertion to slice 2, where the code is.

---

### MEDIUM 10 — AC1b's suppression rule silently sets a floor of 20 distinct users on every share-bearing test [CODE-CONFIRMED]

AC1b suppresses *every* share below 20 users. AC7's assigned test asserts a share
(`ai_connected` over `set_up_ai_agent` holders). AC1a's numbers carry period-over-period
comparisons, also suppressed under 20.

So any AC7 or AC1a test written against the **rendered page** must seed ≥20 distinct users or
it will assert a percentage that correctly is not there — and the test will be "fixed" by
lowering the threshold or by asserting the suppressed form, which quietly guts AC7. Nothing in
the fixtures section budgets a 20+ user fixture; `make_user(internal=False)` and a match
factory are all it names. Either state that AC7 is asserted at the read model (below the
suppression layer) or budget the fixture size explicitly.

---

### MEDIUM 11 — The OAuth harness is buildable but its stated proof is not what it will prove, and it has one concrete time sink [CODE-CONFIRMED]

Sizing it honestly:

- `app/auth/google.py` registers the client with
  `server_metadata_url="https://accounts.google.com/.well-known/openid-configuration"`. Any
  real call to `authorize_access_token` fetches that document over the network. The harness
  must therefore stub at the `oauth.google` object, not at the transport — and that is the
  half-day nobody has budgeted, because it is the first time in this repo.
- Once stubbed, the rest of `google_callback` (`app/routes/auth.py:88-115`) runs for real:
  `sync_google_user`, `await db.commit()`, `set_session_user`. That genuinely exercises the
  feature's wiring, so the harness *is* worth building.

But slice 5's must-prove says *"survives navigation + OAuth"*. With `authorize_access_token`
stubbed, Authlib's state round trip is gone, and what remains is "a dict placed in
`request.session` on request 1 is still there on request 2" — a property of Starlette's
`SessionMiddleware`, not of this feature. Reword the must-prove to what the harness can
actually prove: *"first touch captured on the landing request is read by `sync_google_user`
and written to the new user's row."* That is the real risk, it is testable, and it is not what
the current wording asks for.

---

### MEDIUM 12 — Slice 6's merge gate is a manual, un-tooled step against a database no test ever touches [CODE-CONFIRMED]

Slice 6's must-prove ends with *"dry-run readback against a production copy before merge"*.
Everything about that is unowned:

- No script. `scripts/` has `offline_db.py` and nothing for pulling or restoring a prod copy.
- No command, no expected row counts, no artifact to attach to the PR.
- Production is Postgres. Every one of the 28 tests in `tests/test_migrations.py` runs
  `_run_alembic` in a subprocess against a throwaway **SQLite** file, and CI has no Postgres
  at all. So the backfill's SQL is authored and tested on one dialect and gated on a manual
  run against the other.
- The chain is 50 migrations deep, and the dev DB alone has 1,648 matches / 20,276 submissions.

This is the highest-consequence step in the plan — the plan says both backfills are
irreversible in practice and ship on merge — and it is the only one with no mechanised proof.
At minimum: name the command, name the readback query, and state the numbers that make it a
pass. Better: assert the backfill's SQL against Postgres semantics in a test, even if the run
is SQLite (e.g. no dialect-specific syntax, batched UPDATE, deterministic ordering).

---

### MEDIUM 13 — The mapping table's AC labels have no counterpart in the machine-readable list [CODE-CONFIRMED]

*(The staleness itself was round 1's HIGH 2, accepted; the consequence for the new mapping
table is new, which is why it is here.)*

`state.json` `/discovery/acceptance_criteria` still holds **22** items in the revision-3
shape. Item 0 still reads *"403 for a non-admin"*. There is no AC0, no AC1a, no AC1b. Index N
corresponds to spec AC N+1, so the whole list is offset from the numbering the mapping table
uses.

Effect on revision 2's fix specifically: of the eight rows in the new table, **AC1a and AC1b
have no entry at all** in the authoritative list, and the other six point at the wrong index.
Whatever downstream stage expands `state.json` into tasks will not generate the two tests the
mapping table was written to add. The table reads as a fix and is inert where it matters most
— the three summary numbers, which the plan itself notes have lost their tests three rounds
running.

---

### LOW 14 — Revision 2's `ValueError` catch is unreachable on the path it was added for, and untestable on SQLite [CODE-CONFIRMED]

The recorder now catches `ValueError` because *"`StringLengthExceeded` is a `ValueError`"*.
But `StringLengthExceeded` is raised by `app/sqlite_parity.py`'s `before_flush` guard, and that
module documents its own limit:

> Limitation: this only sees ORM inserts/updates. Core `insert()`/`update()` statements bypass
> the flush and are not checked here.

The plan's write mechanism is a **Core** `conn.execute(insert(UserMilestone), row)`. The guard
never fires for it. So on SQLite an over-long `milestone` / `source_match_id` is stored
happily, and on Postgres it raises `DataError`. The added catch is dead code for the listener
path, and no test in this suite can produce the condition it guards. Harmless with today's
values (`ai`, `human`, `M_…`), but it should not be recorded as a covered hazard.

---

### LOW 15 — AC18's assigned test names a fixture size but not the predicate under test [CODE-CONFIRMED]

The assignment is *"handle-less user labelled; 51 stuck users → 50 rows + remainder"*. The cap
half is sharp and will discriminate. The other two halves are not writable as stated:

- **"51 stuck users"** — the plan never defines *stuck*. Is a user who reached `played_turn`
  stuck? A user with `signed_up` only? The stuck list is described once, in the spec's build
  list, as "users and their furthest milestone". A test built against whatever predicate the
  implementer invents is tautological.
- **"handle-less user labelled"** — `tests/factories.py:make_user` always sets both `handle`
  and `handle_key` (`resolved_handle = handle or f"agent{i}"`). The fixture needs an explicit
  way to produce a handle-less user; the plan's fixture note only adds `internal=False`.

---

## Residual Risks

- **The eight-row mapping table is a real improvement.** Six of the eight rows name a test
  that would fail against the obvious broken implementation: AC6 (a strict-nesting reader
  returns 0 where an independent reader returns 1), AC7 (the two denominators give different
  ratios), AC9 (an internal user changes the count), AC18's cap (50 rows + remainder = 1 at
  n=51), AC1a's computation half, AC19's presence check. That is a genuine gain over
  revision 1, and the finding above about the other two rows should not be read as dismissing it.
- **`after_flush_postexec` was verified working here**, independent of round 1: with the
  plan's exact shape a milestone is visible in a new session after the caller's commit
  (probe case A = 1). The mechanism section is right; only the Risks table is stale.
- **The re-entrancy hazard the plan states has no assigned test.** *"Postexec writes must be
  guarded against re-entrancy or they hit `FlushError` after 100 flushes."* Slice 2's
  must-prove has no re-entrancy item. In practice a Core insert on the connection does not
  dirty the session, so the hazard may be moot — but the plan asserts it and then proves
  nothing about it, which is the worst of both.
- **The `StaticPool` fact from HIGH 1 has a second consequence nobody has costed.** Because
  listeners are registered on the `Session` class at import, and `tests/factories.py`'s
  `make_user` / `make_agent` / `seat_player` all flush, every existing integration test starts
  writing `user_milestones` rows into a shared connection. Round 1 flagged the blast radius;
  what is new is that with one connection per engine, a milestone write that escapes its
  transaction (case B above) is visible to everything else in that test. Fixture-ordering
  surprises are more likely than the plan's "checked at the end of slice 2" implies.
- **Fast-lane cost.** All of the milestone recorder tests will request `db` or `reset_db` and
  land in the `integration` lane, so the fast lane is not affected by them — but the migration
  tests still are (they use `tmp_path` + subprocess, which the auto-tagger in
  `tests/conftest.py:52-68` does not catch), and slices 1, 4 and 6 each add at least one.
- **Reviewer independence.** This is a Claude lens on a Claude-authored plan, the same caveat
  the spec records. The findings that were *executed* (HIGH 1, HIGH 2) and the ones read
  directly out of files (HIGH 3, HIGH 4, MEDIUM 8, MEDIUM 13, LOW 14, LOW 15) do not depend on
  that judgement; the rest is reasoning over read code.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "The fresh-session-after-commit assertion cannot catch a missing commit, and no SQLite-only test can", "detail": "Measured: app/db.py's in-memory engine uses StaticPool so every 'fresh session' is the same DBAPI connection, and running the plan's exact recorder shape shows a caller that never commits still leaves the milestone visible (1 row) while its own parent row rolls back — so the correct and broken implementations return the identical answer, and CI has no Postgres service to tell them apart."}, {"severity": "HIGH", "title": "The Risks table still prescribes the refuted after_commit shape", "detail": "Plan line 266 says 'Collect in the listener, write in after_commit; slice 2 exists to prove this shape', which the mechanism section 130 lines above refutes — re-executed here: the hook raises InvalidRequestError, the caller's commit reports OK, and zero rows persist."}, {"severity": "HIGH", "title": "Re-verified from scratch: the AC coverage claim is still false for a different seven criteria", "detail": "Revision 2 closed the seven ACs with no test at all but left round 1's four partials (AC13 collision half, AC16 browser-default half, AC22 capture half, AC1 submenu membership) and exposed three more — AC4's 'returned' half, AC10's 'set at all three creation sites', and AC20's reversibility for the three migrations added in slices 4, 5 and 6."}, {"severity": "HIGH", "title": "Two of the eight new assignments name a test that cannot be written from this plan", "detail": "AC14 needs a column that distinguishes 'captured, direct' from 'never captured' and the plan names none — D9's precedence rule actually produces 'direct' for an uncaptured user, the exact bug AC14 forbids; AC19 needs the dated reconstructed-history line but the plan's config keys are only FIRST_TOUCH_CAPTURE_ENABLED and INTERNAL_EMAIL_DOMAINS, with no source for the deploy date."}, {"severity": "MEDIUM", "title": "AC1b's n=19/n=21 pair steps over the boundary the rule turns on", "detail": "The spec suppresses shares 'below 20 users', so the likely off-by-one implementation (suppress when n<=20) passes both assigned cases and is wrong at exactly n=20; the pair should be 19 and 20."}, {"severity": "MEDIUM", "title": "AC1a's assignment is ambiguous between a label check and a computation check", "detail": "AC1a literally requires each summary number to STATE its window, population and internal exclusion, but the assignment reads as a computation assertion — whichever the implementer builds, the other half passes untested, and the numbers are computed in slice 7 while the criterion is assigned to slice 8."}, {"severity": "MEDIUM", "title": "AC9 is a quantifier over four surfaces and is assigned as one bullet", "detail": "'Excluded everywhere' is carried as a single slice-7 item, so one test on the milestone counts passes while signup_sources, the stuck list and slice 8's three summary numbers still include internal accounts — which D10 measured at over 500 of 646 player rows."}, {"severity": "MEDIUM", "title": "Revision 2's corrections landed in prose but not in the must-prove table the checkpoint reads", "detail": "Slice 3 still demands a connector-fallback test the Fixtures note calls vacuous, and slice 4 still demands 'backfill and creation rule agree on one fixture set' that the harness-gaps note says cannot be a single test."}, {"severity": "MEDIUM", "title": "The bot-seat exclusion is built in slice 2 but proved in slice 3", "detail": "The house-bots-user exclusion is Player-listener code delivered in slice 2, yet 'bot seats record no joined_match' is assigned to slice 3, so slice 2's checkpoint goes green against an implementation that records a joined_match for every seat bots/seating.py:151 creates."}, {"severity": "MEDIUM", "title": "AC1b's suppression rule sets an unbudgeted floor of 20 users on every share-bearing test", "detail": "AC7's share and AC1a's period comparisons are suppressed below 20 users, so any page-level test of them needs a 20+ distinct-user fixture that the fixtures section does not budget, and the cheap 'fix' when it fails is to weaken the threshold or assert the suppressed form."}, {"severity": "MEDIUM", "title": "The OAuth harness proves something narrower than slice 5 claims, and has one concrete time sink", "detail": "app/auth/google.py registers the client with a live server_metadata_url so the harness must stub oauth.google itself (a first for this repo), after which 'survives the OAuth round trip' reduces to 'a session dict survives two requests' — a SessionMiddleware property, not a feature property; the real testable claim is that sync_google_user reads first touch and writes it to the new user's row."}, {"severity": "MEDIUM", "title": "Slice 6's merge gate is manual, un-tooled, and aimed at a dialect no test touches", "detail": "'Dry-run readback against a production copy before merge' names no script, command, query or pass threshold, while all 28 tests in tests/test_migrations.py run Alembic against a throwaway SQLite file and CI has no Postgres — so the most irreversible step in the plan is the only one with no mechanised proof."}, {"severity": "MEDIUM", "title": "The mapping table's AC labels have no counterpart in the machine-readable list", "detail": "state.json still holds 22 revision-3 criteria (item 0 reads '403 for a non-admin'), so AC1a and AC1b — the two rows covering the three summary numbers that have lost their tests three rounds running — have no entry at all, and the other six rows are offset by one from the list that drives task generation."}, {"severity": "LOW", "title": "The ValueError catch added in revision 2 is unreachable on the Core-insert path", "detail": "StringLengthExceeded comes from app/sqlite_parity.py's before_flush guard, which documents that it never sees Core insert()/update() — the plan's write mechanism — so the catch is dead code for listeners and the condition cannot be produced by any SQLite test."}, {"severity": "LOW", "title": "AC18's assigned test names a fixture size but not the predicate under test", "detail": "The plan never defines what makes a user 'stuck', so '51 stuck users' is built against whatever predicate the implementer invents, and tests/factories.py:make_user always sets handle and handle_key, so the handle-less label case needs a factory change the fixtures note does not mention."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 2. 15 findings (4 HIGH), two verified by execution. Decisive and independently corroborated by the implementation lens: revision 2's 'assert from a fresh session after commit' CANNOT FAIL, because app/db.py's in-memory engine uses StaticPool so every fresh session is the same DBAPI connection - correct and broken implementations return identical answers, and CI has no Postgres service. Worse than no mitigation because it manufactures confidence. Replaced with three graded options. Also accepted: coverage re-verified from scratch and still false for a DIFFERENT seven ACs - four partials from round 1 untouched plus three newly exposed (AC4's returned half, AC10's three-creation-sites, AC20's reversibility for the slice 4/5/6 migrations); this is the fourth consecutive round the coverage claim has been false and is recorded as a drafting blind spot. AC14 was unwritable as specified - no column distinguished captured-and-direct from never-captured, and D9's precedence rule PRODUCES the bug AC14 forbids; fixed by making first_source_channel three-valued. Credit recorded: six of the eight mapping rows do discriminate, and after_flush_postexec verified working independently.
