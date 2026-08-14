---
reviewer: "claude"
lens: "feasibility-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/spec.md"
artifact_sha256: "d848439036d0b0fd68345a85c8d295aefdc43f5286f5252c99c6a5208efa84d1"
repo_root: "."
git_head_sha: "47f8c864a2d8d78ce29635ef606af53b83e59bc3"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "Round 3. 16 findings (6 HIGH), all code-confirmed; the reviewer empirically ran the transaction question against this repo's SQLAlchemy rather than reasoning about it. Decisive: 'advisory, never blocking' had no mechanism - db.add alone surfaces IntegrityError at the caller's commit and add+flush leaves PendingRollbackError, so only db.begin_nested() works, and UNIQUE raises rather than no-opping with no dialect-agnostic on_conflict available. Fixed in new section D3a. Also decisive: the D4 backfill OVERCOUNTS rather than producing a floor, because autopilot rows carry was_defaulted=False and a real submitted_at - fixed via players.autopilot_at, with the over-exclusion accepted and stated."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/spec.claude.feasibility-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec feasibility-adversarial

## Findings

### HIGH 1 — "Advisory, never blocking" is not implementable with `record_milestone(db, ...)` as written [CODE-CONFIRMED]

D3 gives the recorder the caller's session (`record_milestone(db, user_id,
milestone)`) and says a failure must never break the triggering request. On
SQLAlchemy 2.0 (`sqlalchemy[asyncio]>=2.0.30`, lock resolves 2.0.x) that is false
in both obvious implementations. I ran the three shapes against the repo's own
SQLAlchemy and aiosqlite:

| Recorder shape | What actually happens |
|---|---|
| `db.add(Milestone(...))` only | The `try/except` catches **nothing**. The `IntegrityError` surfaces at the **caller's** `db.commit()`. The triggering request 500s. |
| `db.add(...)` + `await db.flush()` inside the `try` | The recorder catches the `IntegrityError`, then the caller's `db.commit()` raises `PendingRollbackError`. Request 500s **and** the business write is lost. |
| `async with db.begin_nested():` (SAVEPOINT) | Works. Recorder catches; caller commits; the business row persists. |

So the design is buildable, but only via an explicit SAVEPOINT, and the spec
never mentions one. Left to an implementer, shape 1 is the natural reading of
"call `record_milestone(db, ...)` and wrap it" — and it is the one that fails
silently in review and loudly in prod.

This is not a nit about wording: AC22 ("a failure logs and never breaks the
request") and the test-plan item "Recorder raising does not fail the triggering
request" both pass trivially if the test injects a Python exception, and both
miss the real failure mode, which is a **database** error inside a shared
transaction. Every call site listed in D3 is a pre-commit site.

Required in the plan: `record_milestone` must open `db.begin_nested()`, catch
`SQLAlchemyError` only inside that block, and the test must force a real DB error
(duplicate key / oversize string), not `raise RuntimeError`.

### HIGH 2 — `UNIQUE(user_id, milestone)` does not make a repeat write "a no-op, not an error" [CODE-CONFIRMED]

D1's central claim is that the unique constraint makes recording idempotent. It
does not. On both dialects the constraint **raises**; a no-op requires an
explicit `ON CONFLICT DO NOTHING`, and SQLAlchemy has no dialect-agnostic form of
it — `sqlalchemy.insert(...).on_conflict_do_nothing()` is an `AttributeError`
(verified). You must import `sqlalchemy.dialects.postgresql.insert` for prod and
`sqlalchemy.dialects.sqlite.insert` for dev/tests and branch on
`db.get_bind().dialect.name`.

The repo has **zero** existing `on_conflict` usage anywhere in `app/` or
`mcp_server/`, so this is new machinery with no local precedent. The nearest
existing pattern is dialect-branching by hand, e.g.
`app/services/admin_user_actions.py:110` (`dialect.name != "sqlite"`) and
`migrations/versions/0023_connection_agent_split.py:366`.

The dev/prod parity trap is the dangerous part. On SQLite, a `SELECT`-then-
`INSERT` recorder is effectively single-writer and will look perfectly idempotent
in the test suite. On Postgres with concurrent requests it races. And the failure
mode of the race is HIGH 1: a caught `IntegrityError` that poisons the caller's
transaction. This repo already keeps `app/sqlite_parity.py` specifically because
"passes on SQLite, 500s on Postgres" is a recurring bug class here.

### HIGH 3 — The listed call sites cannot satisfy AC4; human play is missing at two milestones [CODE-CONFIRMED]

"What we are building" item 6 names `agents_create.py` and `web_join.py`. Neither
is on the human path — the very path revision 3 exists to fix.

`Agent(...)` is constructed in **three** places:

- `app/routes/agents_create.py:181` (listed)
- `app/engine/human_player.py:98` — `get_or_create_human_agent`, the `kind=human` agent (**not listed**)
- `app/engine/bots/seating.py:120` (not listed; house-owned, correctly out of scope but must be a deliberate exclusion)

`Player(...)` is constructed in **three** places:

- `app/routes/web_join.py:339` (listed)
- `app/routes/web_play.py:313` — `seat_human_player`, the human seat (**not listed**)
- `app/engine/bots/seating.py:151` (not listed)

As specified, a human who signs up, seats themselves and plays records neither
`set_up_a_way_to_play` nor `joined_match`. AC4 — the headline acceptance
criterion of the redesign — fails. AC10's "backfill and creation rule share one
predicate" discipline needs a twin here: the plan must enumerate creation sites
from `grep`, not from memory.

### HIGH 4 — There is no single chokepoint for `played_turn`, and the one shared helper is shared with autopilot [CODE-CONFIRMED]

Item 6 says "the submission path", singular. There is no such thing. Four
functions write a `TurnSubmission`:

| Site | Reached by | Should record? |
|---|---|---|
| `app/engine/agent_play.py:269` (`submit_action`) | AI agents, HTTP **and** MCP (MCP proxies through this) | yes, unless connector fallback |
| `app/routes/web_play.py:253` → `record_player_action` | human web play | **yes** |
| `app/engine/bots/service.py:124` → `record_player_action` | scripted bots | no |
| `app/engine/bots/service.py:~195` `_auto_submit_autopilot` → `module.record_submission` | departed humans on autopilot | no |
| `app/games/hoard_hurt_help/scoring.py:190` | missed-deadline default | no |

The two tempting chokepoints are both wrong. `player_move.record_player_action`
is shared by human play **and** bots. `module.record_submission` is shared by
everything including the defaulter. So the hook must be duplicated at exactly two
request-level sites — `agent_play.submit_action` and the `web_play` act handler —
and the plan must state that `record_player_action` and `record_submission` are
off-limits. Getting this wrong in either direction is invisible in tests written
from the spec (a bot recording `played_turn` for a house user looks like nothing;
a human never recording it looks like AC4 failing for an unrelated reason).

Also note the autopilot writer runs inside the **scheduler background task**, not
a request — so it has no user request to be "advisory" toward, another reason it
must never reach the recorder.

### HIGH 5 — The D4 backfill will *overcount* `played_turn`, not undercount it [CODE-CONFIRMED]

D4 promises the backfill produces "floors, not totals". It cannot, because
autopilot rows are indistinguishable from genuine ones in the only columns the
backfill can filter on. `_auto_submit_autopilot` calls
`module.record_submission(db, turn, player, move, existing=...)` with
`is_connector_fallback` left at its default `False`, and
`hoard_hurt_help/game.py:220` sets `was_defaulted = is_connector_fallback`. So an
autopilot HOARD lands with `was_defaulted=False` **and** a real `submitted_at`
(`game.py:238`). D5 item 3 already says this; D4 then quietly assumes the
backfill filter can be `was_defaulted=False AND submitted_at IS NOT NULL`, which
counts every abandoner as a player.

That is the exact opposite of the stated direction of error, and it lands on the
number the page most invites a reader to trust. The page note ("undercount
abandonment") would be actively wrong.

There is a usable fix the spec does not mention: `players.autopilot_at` exists
(`app/models/player.py:71`, migration 0042). The backfill can exclude
`submitted_at >= autopilot_at` for players with a non-null `autopilot_at`. It is
approximate — one timestamp, and a player can be put on autopilot only once — but
it turns an overcount into a genuine floor. The plan must say so explicitly.

### HIGH 6 — `ai_connected` misses the connector / HTTP-API path entirely [CODE-CONFIRMED]

Item 6 lists `mcp_connection.py`. `first_connected_at` is stamped in **five**
places across **two** modules:

- `app/engine/mcp_connection.py:145`, `:174`, `:206`, and `:222` (the `Connection(...)` constructor)
- `app/engine/connection_activity.py:119` — `mark_seen`

`mark_seen` is the one that matters most. Its own docstring says it is "called
from the single auth choke point (`require_connection`), so it covers every
connection method (runner, MCP, direct API)". It is how a connection-key
connector — the always-on machine connector, and every direct `/api` agent —
first registers as connected. A user on that path never records `ai_connected`,
so D2's "share of users who chose an AI agent" is wrong by however many people
use a key instead of MCP OAuth.

Two implementation notes the plan needs: `mark_seen` writes with a Core
`update()` (so `app/sqlite_parity.py`'s ORM `before_flush` guard does not see it)
and it calls `await db.commit()` itself at line 128 — so a recorder call there
sits on a different side of the commit boundary than every other call site in D3.

## Findings — MEDIUM

### MEDIUM 7 — The table has no timestamp column, and `returned` needs one [UNVERIFIED]

D1 specifies only `user_id`, `milestone`, and `UNIQUE(user_id, milestone)`. No
`reached_at`. Without it:

- `returned` ("a genuine submission on a **second distinct local day**") has no
  way to know the first day, so it must re-derive it from `turn_submissions` on
  every submission — the table D1 itself says gets hard-deleted by
  `delete_match` (`app/engine/match_deletion.py:62,69`). `returned` would then be
  the one milestone that is *not* deletion-proof, defeating the point.
- The three summary numbers are all window-scoped, and item 7's cohort read is
  "counts per milestone over a signup cohort". `users.created_at` covers signups,
  but nothing else can be windowed.

Add `reached_at` (server-default now, timezone-aware) to the table in the spec,
not as a plan detail.

### MEDIUM 8 — AC16 (browser-timezone return detection) is unachievable with event-time recording [UNVERIFIED]

D11 says return detection uses "the page window's timezone, defaulting to the
browser's". D1/D3 say the milestone is a row written once, at submission time,
server-side. A row written in one timezone cannot be re-evaluated in a viewer's
timezone later. Two admins in different timezones would see the same number, and
neither would see their own.

This is a genuine cost of the redesign, not an oversight to patch: revision 2's
derived funnel could honour AC16; revision 3's durable row cannot. Either drop
AC16 to "the server's timezone, stated on the page", or store `reached_at` and
compute `returned` at read time (which makes it a derived metric again, not a
milestone). Pick one in the spec.

### MEDIUM 9 — AC17 (smoke-test exclusion) is also unachievable at read time [CODE-CONFIRMED]

Smoke-test exclusion is a **match-name** predicate:
`~Match.name.ilike(f"{TEST_NAME_PREFIX}%")` with `TEST_NAME_PREFIX = "prod
smoke"` (`app/read_models/admin_reports.py:109`, `app/match_naming.py:14`). The
milestone row carries no match reference, so a `played_turn` earned in a smoke
match can never be filtered out afterwards. It must be filtered at the call
site — which the `record_milestone(db, user_id, milestone)` signature has no room
for. Same for `joined_match`.

Fix is small (pass the match, or guard at the call site) but it must be in the
spec, because "excluded everywhere" reads as a read-model concern and will be
implemented in the read model, where it silently does nothing.

### MEDIUM 10 — D5 names three non-genuine submission kinds; there is a fourth [CODE-CONFIRMED]

Connector fallbacks. `app/engine/agent_play.py:220` sets `was_defaulted =
is_connector_fallback`, so a fallback move — the server playing HOARD because the
connector's agent did not answer — is written through the same
`submit_action` an implementer will hook. AC8 lists defaulted, NULL-timestamp and
autopilot only.

The mitigation is one line away and already in the file:
`agent_play.py:292` reads `if not is_connector_fallback: await
increment_turns_played(...)`. The recorder call belongs behind the same guard.
Naming it in AC8 costs nothing and prevents an abandoning connector from clearing
"played a turn" — precisely the failure D5 exists to stop.

### MEDIUM 11 — The migration cannot cleanly share the internal predicate, and needs dialect-branched SQL [CODE-CONFIRMED]

Three separate constraints collide in D4/D10:

1. **No ORM.** Alembic runs sync via psycopg2 (`pyproject.toml:15`,
   `migrations/env.py:22`). Every existing data migration uses raw
   `op.execute(sa.text(...))` (e.g. `0023:349`). The backfill must be raw SQL —
   it cannot reuse `app/identity/internal_accounts.py` as a Python predicate over
   ORM objects, only as a source of literal domain strings.
2. **Import coupling.** D10 requires the backfill and creation rule to "use one
   shared predicate so they cannot disagree". A versioned migration importing a
   live app module means the historical migration's behaviour changes whenever
   that module changes — and `tests/test_migrations.py` replays the whole chain
   from base on every run. The honest resolution is to share the *domain list*
   constant and duplicate the SQL, with a test asserting the two agree on one
   fixture set (which the test plan already implies).
3. **Dialect-branched date SQL.** `returned` needs distinct-day grouping:
   `date(submitted_at)` on SQLite versus `(submitted_at AT TIME ZONE ...)::date`
   on Postgres. Migration 0023 already branches this way at line 366; the spec
   should say the backfill does too.

### MEDIUM 12 — The backfill has no test harness, and the spec budgets none [CODE-CONFIRMED]

The suite builds its schema from `Base.metadata.create_all`
(`tests/conftest.py:150`), so migrations are never exercised there.
`tests/test_migrations.py` runs the real chain, but only via `subprocess` against
an **empty** throwaway SQLite file — there is no data seeding and no
post-migration data assertion beyond schema shape (`sqlite3.connect` + PRAGMA
checks). The test-plan item "Milestone backfill produces floors consistent with
surviving rows" needs a new harness: seed rows into the file DB between
`upgrade <n-1>` and `upgrade head`. That is real work the plan must schedule, and
without it the backfill ships unverified against a 20k-row prod table.

### MEDIUM 13 — `signed_up` has no separate-session escape hatch, so HIGH 1's savepoint is mandatory [CODE-CONFIRMED]

Worth stating because it closes off the obvious alternative fix. The repo's only
existing fail-open DB-write precedent is
`app/request_logging.py:104-112`, which opens its **own** `SessionLocal()` so a
failure can never touch the caller's transaction. That pattern cannot be reused
for `signed_up`: `sync_google_user` only `flush()`es the new `User`
(`app/routes/auth.py:49`) and the caller commits one line later
(`auth.py:106-107`). A second session would not see the id, and an FK from
`user_milestones.user_id` would then fail on its own commit.

So the recorder must live on the caller's session, which makes the SAVEPOINT in
HIGH 1 the only workable shape — not a stylistic choice.

## Findings — LOW

### LOW 14 — Extra write per event: fine everywhere except `returned` [CODE-CONFIRMED]

Honest answer on the performance question: this is a single-instance app
(`railway.json` `numReplicas: 1`) with match-scale traffic, and
`agent_play.submit_action` already does two commits plus a `SELECT` per
submission (`agent_play.py:290-294`). One more guarded statement is noise.

The guarded part matters. `signed_up` sits inside `if user is None`,
`ai_connected` inside `if first`, `picked_handle` inside the set. `returned` has
no such cheap transition — it needs a lookback on **every** submission unless the
plan gives it one (e.g. skip entirely once the `returned` row exists, checked by
the same index). On Postgres each `begin_nested()` also costs a `SAVEPOINT` +
`RELEASE` round trip. Both are cheap; both need to be stated so the implementer
does not put an unbounded `GROUP BY date(...)` scan on the turn path.

### LOW 15 — `picked_handle` has a second set site [CODE-CONFIRMED]

`app/routes/dev_login.py:56` creates the user with `handle_key` already
populated, so the handle milestone never fires on that path. Dev-login users are
internal and excluded from the page anyway, so the impact is nil — but D3's "one
recorder, called from the event sites" list is incomplete in the same way HIGH 3
and HIGH 6 are, and the plan should enumerate rather than assume.

### LOW 16 — Backfill runs pre-deploy, while the old code is still serving [CODE-CONFIRMED]

`railway.json` sets `"preDeployCommand": ["alembic upgrade head"]`. The backfill
therefore completes **before** the milestone-recording code goes live, against a
database the old app is still writing to. Events in that window get neither a
backfilled row nor a recorded one. The window is short, and D4's "floors" framing
covers it — but the post-deploy verification D10 already asks for (read back the
flagged row count) should also spot-check that the first live signup after cutover
recorded `signed_up`, so a wiring mistake is caught in minutes rather than at the
first weekly read.

## Residual Risks

- **Not measured: prod data volume.** The spec cites 20,276 turn-submission rows
  in the dev DB. I could not read the prod DB from here, so the backfill's
  runtime inside Railway's pre-deploy step is unverified. At dev scale a handful
  of `INSERT ... SELECT` passes is seconds; if prod is an order of magnitude
  larger the pre-deploy timeout becomes a live risk. Worth one `SELECT count(*)`
  against prod before the plan checkpoint.

- **Savepoint behaviour on asyncpg not directly tested.** I verified the three
  transaction shapes on aiosqlite (the test/dev driver). SAVEPOINT semantics are
  standard on Postgres and SQLAlchemy handles both, but the fix for HIGH 1 should
  get one integration-style test that forces a real duplicate-key error rather
  than relying on the SQLite result.

- **`returned` semantics still unsettled.** MEDIUM 8 offers two exits (drop the
  browser timezone, or make `returned` derived). Both are design decisions for
  Chris, not implementation details — and the choice changes the table schema, so
  it should be resolved in the spec rather than deferred to the plan.

- **Concurrency of the recorder under real MCP load.** MEMORY notes a single
  match producing 1,164 MCP calls in 26 minutes across 8 agents. The recorder's
  `ON CONFLICT` path will be genuinely concurrent on Postgres in a way the SQLite
  test suite cannot reproduce. The dialect branch (HIGH 2) is the mitigation, but
  it will only ever be exercised for real in prod.

- **Lens boundary.** I did not review the read models, the page, the template
  changes, the nav surfaces, the first-touch middleware's cookie-size behaviour,
  or D8's privacy obligation, except where they collided with buildability
  (MEDIUM 8, MEDIUM 9). Those belong to the requirements and security lenses.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Advisory milestone write on the caller's session cannot fail open", "detail": "Verified on the repo's SQLAlchemy 2.0: db.add-only lets the IntegrityError surface at the caller's commit, and add+flush inside a try leaves the caller with PendingRollbackError, so only an unmentioned db.begin_nested() SAVEPOINT makes D3's advisory contract real."}, {"severity": "HIGH", "title": "UNIQUE(user_id, milestone) raises rather than no-opping", "detail": "D1's idempotency premise is wrong: the constraint raises IntegrityError on both dialects and SQLAlchemy has no dialect-agnostic on_conflict_do_nothing (verified AttributeError), so the recorder needs dialect-branched postgresql/sqlite insert and the repo has no existing on_conflict precedent."}, {"severity": "HIGH", "title": "Listed call sites miss the human path, so AC4 cannot pass", "detail": "Agent rows are created at agents_create.py:181, human_player.py:98 and bots/seating.py:120, and Player rows at web_join.py:339, web_play.py:313 and bots/seating.py:151, but item 6 lists only agents_create.py and web_join.py, so a human player records neither set_up_a_way_to_play nor joined_match."}, {"severity": "HIGH", "title": "No single chokepoint for played_turn; the shared helper is shared with autopilot", "detail": "record_player_action is called by both web_play.py:253 (human) and bots/service.py:124 (bots), and module.record_submission is additionally called by _auto_submit_autopilot and scoring.py:190, so the hook must be duplicated at agent_play.submit_action plus the web_play act handler and must never live in either shared helper."}, {"severity": "HIGH", "title": "D4 backfill overcounts played_turn instead of producing a floor", "detail": "Autopilot submissions are written with was_defaulted=False and a real submitted_at (bots/service.py _auto_submit_autopilot into game.py:220/238), so the only available backfill filter counts abandoners as players; players.autopilot_at (models/player.py:71) is the unmentioned fix."}, {"severity": "HIGH", "title": "ai_connected misses the connector and direct-API path", "detail": "first_connected_at is stamped at mcp_connection.py:145/174/206/222 and at connection_activity.py:119 (mark_seen, the single auth chokepoint covering runner, MCP and direct API, writing via Core update and committing itself), but item 6 lists only mcp_connection.py."}, {"severity": "MEDIUM", "title": "Milestone table has no timestamp column", "detail": "D1 specifies only user_id and milestone, so returned must re-derive the first play day from turn_submissions - the table delete_match hard-deletes - and none of the window-scoped numbers can be computed; add reached_at to the spec."}, {"severity": "MEDIUM", "title": "AC16 browser-timezone return detection is impossible with event-time recording", "detail": "A row written once at submission time freezes one timezone decision and cannot be re-evaluated per viewer, so D11 contradicts D1/D3 and either AC16 must drop to server timezone or returned must become a read-time derived metric."}, {"severity": "MEDIUM", "title": "AC17 smoke-test exclusion cannot be applied at read time", "detail": "Exclusion is a match-name predicate (~Match.name.ilike('prod smoke%'), admin_reports.py:109) but the milestone row carries no match reference, so it must be filtered at the call site and record_milestone(db, user_id, milestone) has no room for that argument."}, {"severity": "MEDIUM", "title": "D5 omits connector fallbacks as a fourth non-genuine submission", "detail": "agent_play.py:220 sets was_defaulted = is_connector_fallback so a server-played fallback flows through the same submit_action an implementer will hook, and AC8 should name it behind the existing 'if not is_connector_fallback' guard at agent_play.py:292."}, {"severity": "MEDIUM", "title": "Migration cannot share the internal predicate and needs dialect-branched date SQL", "detail": "Alembic runs sync via psycopg2 with no ORM (env.py:22, pyproject.toml:15) so the backfill must be raw SQL, importing a live app module into a versioned migration breaks chain replay under tests/test_migrations.py, and returned's distinct-day grouping differs between SQLite date() and Postgres AT TIME ZONE."}, {"severity": "MEDIUM", "title": "No test harness exists for a data backfill", "detail": "The suite builds schema via Base.metadata.create_all (conftest.py:150) and test_migrations.py runs the chain by subprocess against an empty SQLite file with no seeding, so the planned 'backfill produces floors' test needs new harness work the spec does not budget."}, {"severity": "MEDIUM", "title": "signed_up has no separate-session escape hatch", "detail": "The repo's only fail-open DB-write precedent (request_logging.py:104-112) opens its own SessionLocal, which cannot work for signed_up because sync_google_user only flushes the User (auth.py:49) and the caller commits at auth.py:107, so the SAVEPOINT of HIGH 1 is mandatory rather than optional."}, {"severity": "LOW", "title": "Extra write per event is cheap except for returned", "detail": "Single-instance app already doing two commits per submission absorbs one guarded statement, but returned has no cheap transition guard and would otherwise put a per-submission distinct-day lookback on the turn path."}, {"severity": "LOW", "title": "picked_handle has a second set site", "detail": "dev_login.py:56 creates the user with handle_key already populated so the milestone never fires there; impact is nil since dev users are internal, but the call-site list is incomplete in the same way as HIGH 3 and HIGH 6."}, {"severity": "LOW", "title": "Backfill runs pre-deploy while old code still serves", "detail": "railway.json runs 'alembic upgrade head' as preDeployCommand, so events between backfill and cutover get neither a backfilled nor a recorded row, and the post-deploy check should confirm the first live signup after cutover recorded signed_up."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: Round 3. 16 findings (6 HIGH), all code-confirmed; the reviewer empirically ran the transaction question against this repo's SQLAlchemy rather than reasoning about it. Decisive: 'advisory, never blocking' had no mechanism - db.add alone surfaces IntegrityError at the caller's commit and add+flush leaves PendingRollbackError, so only db.begin_nested() works, and UNIQUE raises rather than no-opping with no dialect-agnostic on_conflict available. Fixed in new section D3a. Also decisive: the D4 backfill OVERCOUNTS rather than producing a floor, because autopilot rows carry was_defaulted=False and a real submitted_at - fixed via players.autopilot_at, with the over-exclusion accepted and stated.
