---
reviewer: "claude"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/admin-engagement-dashboard/plan.md"
artifact_sha256: "92e652d2a16a9f14388d71b8b9e0804b88eb18af7a7f733295eb61f3c8d61d15"
repo_root: "."
git_head_sha: "92d75f04fcf9d26cc10261576f4658cb187b56c3"
git_base_ref: "origin/main"
git_base_sha: "0a38ccf04bbb00ad4e47446f20ebd95638a0d4a1"
generation_method: "claude-subagent"
resolution_status: "accepted"
resolution_note: "13 findings (5 HIGH). Also executed rather than argued. Independently confirmed the after_commit breakage and, decisively, that slice 2's own must-prove list would have PASSED against the broken shape - the checkpoint would have gone green while recording nothing, and slices 3-8 would have built on it. Slice 2's proof list now requires assertions from a fresh session after commit. Also accepted: seven ACs had no test anywhere (AC1a/6/7/9/14/18/19) and are now assigned to owning slices, with a note that the three summary numbers have lost their tests in three consecutive rounds - a drafting blind spot, not bad luck; listeners registered at app startup are unreachable under the test client's ASGITransport (no lifespan); the flag must be read per-request because create_app() runs at import; no test in the repo drives the OAuth callback, so that harness is new work in slice 5; connector-fallback rows are byte-identical to missed deadlines, so the fixture sketch's 'four cases' are two distinguishable shapes. Credit recorded: the savepoint recorder's safety test genuinely discriminates, and except IntegrityError: pass does not trip ruff S110."
raw_output_path: "docs/workflow/feature-runs/admin-engagement-dashboard/reviews/plan.claude.testability-adversarial.review.md.raw.txt"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

*Lens: testability. Every claim below was checked against the real repo; where I could
run the mechanism I ran it (SQLAlchemy 2.0.50 + aiosqlite, the same combination
`tests/conftest.py` builds). Findings are marked [CODE-CONFIRMED], [CODE-REFUTED] or
[UNVERIFIED].*

---

### HIGH 1 — Seven acceptance criteria have no test anywhere in the plan [CODE-CONFIRMED]

The spec says (line 411): *"Every criterion has a matching test in the plan below."*
That is not true of this plan. Mapping all 25 criteria (0, 1, 1a, 1b, 2–22) against both
the slice "must prove" column **and** the seven highest-value tests:

| AC | Covered? | Where |
|---|---|---|
| 0, 1, 2, 3, 4, 5, 8, 10, 11, 15, 17, 20, 21 | yes | slices 1–8 / tests 1–7 |
| **1a** summary numbers state window, population, internal-exclusion | **no test** | absent from every slice |
| **6** counts are independent, no suppression | **no test** | absent |
| **7** `ai_connected` as a share of AI-agent users | **no test** | `agent_kind` column exists "for AC7", nothing proves the ratio |
| **9** bots and internal users excluded from every number | **no test** | slice 7's must-prove has no exclusion item at all |
| **14** uncaptured source renders `"unknown"`, never `"direct"` | **no test** | absent |
| **18** stuck list: handle-less label, cap at 50, remainder count | **no test** | the stuck list is never mentioned in the plan |
| **19** both explanatory notes render | **no test** | absent |
| 12, 13, 16, 22 | partial | see below |

Partials: AC12 loses the OAuth half (finding 9); AC13 keeps "records mcp" and drops "a real
`?utm_source=mcp` does not collide"; AC16 keeps "window timezone" and drops "defaults to the
browser's"; AC22 covers milestone recording and drops first-touch capture ("capture raising
does not fail the page" is in the spec's test plan, not the plan's).

Two of these are repeats, not new gaps. **AC1a exists only because rounds 2 and 3 both found
the summary numbers had no criterion** — its own text says so — and the plan drops their tests
again. **AC9 is the entire point of D10**: the stored `is_internal` flag exists so the page can
exclude internal accounts (over 500 of 646 player rows, per D10). Slice 4 proves the flag is
*stable*; nothing proves the page *uses* it. This is the feature's silent-risk case — a page
that quietly counts ludumlabs and the harness accounts renders perfectly.

**Fix:** add the seven to the slice that owns them (1a/19 → slice 8; 6/7/9/14/18 → slice 7)
before tasks are generated.

---

### HIGH 2 — The authoritative criteria list is stale, so the deploy gate has no machine-readable home [CODE-CONFIRMED]

The spec says the authoritative list "lives in `state.json`, kept in lockstep with this
revision". It is not in lockstep. `state.json` `/discovery/acceptance_criteria` holds **22
items in the revision-3 shape**:

- no **AC0** (`FIRST_TOUCH_CAPTURE_ENABLED=false` ⇒ no cookie, nothing stored) — the privacy
  deploy gate, and the plan's #5 highest-value test;
- no **AC1a**, no **AC1b**;
- item 0 still reads *"403 for a non-admin"*, without the 401-anonymous split round 3 corrected.

Whatever downstream stage consumes `state.json` will therefore never require the one test that
stands between merging and tracking real visitors on an auto-deploying `main`. Either
regenerate the list from spec revision 4 or delete the "authoritative" claim — right now two
lists disagree and the machine-readable one is wrong.

---

### HIGH 3 — The plan's listener write shape (`after_commit`) silently writes nothing [CODE-CONFIRMED, executed]

The Risks table commits to it explicitly: *"Collect in the listener, write in `after_commit`"*.
I ran that shape:

```
=== B: after_insert collects -> after_commit writes (the plan's listener shape) ===
      after_commit FAILED: InvalidRequestError: This session is in 'committed' state;
                           no further SQL can be emitted within this transaction.
  outer commit: OK
  milestones VISIBLE IN A NEW SESSION = 0 (want 1)
```

The insert raises inside the hook, the recorder swallows it as "advisory", **the caller's
commit still reports success**, and no milestone is ever written. The failure mode is total and
invisible. The sibling shape the plan mentions in the same breath does work:

```
=== C: after_insert collects -> after_flush_postexec writes ===
  visible after FLUSH ONLY (no commit) = 1
  visible in a new session after commit = 1
```

The testability problem is the must-prove wording. Slice 2 must prove *"listeners fire for all
three models"* — a listener that fires and writes nothing passes that assertion verbatim. Three
of the six milestones (`signed_up`, `set_up_a_way_to_play`, `joined_match`) ride this path, so
slice 2 can go green with half the feature dead.

**Fix:** change slice 2's must-prove to *"after a `User`/`Agent`/`Player` insert commits, a
**new session** reads the milestone row back"*, and pick `after_flush_postexec` in the plan
rather than offering `after_commit` as an equal option.

---

### HIGH 4 — The advisory-safety test covers one writer and cannot fail for the other [CODE-CONFIRMED, executed]

Plan test #4 ("a failing milestone write leaves the caller's transaction usable") is real and
earns its keep **for the explicit-call path**. I confirmed it discriminates:

```
add-only (spec says broken)      : caller commit FAILED IntegrityError      <-- test FAILS
add+flush in try (spec: broken)  : caller commit FAILED PendingRollbackError <-- test FAILS
begin_nested savepoint           : caller commit OK, parents=2, milestones=1 <-- test PASSES
```

But `record_milestone` is `async def` and opens `db.begin_nested()`. A sync `after_insert` /
`after_flush_postexec` hook cannot `await` it, so the listener path **cannot use the single
writer** — it will emit its own Core insert inside the caller's flush. And that path is
untestable on SQLite:

```
=== E: duplicate insert raised from inside a flush hook ===
      caught IntegrityError in hook
  caller flush+commit: OK
  parents persisted = 1 (want 1)
```

SQLite tolerates a failed statement mid-transaction; PostgreSQL aborts the transaction and every
later statement fails ([UNVERIFIED] here — no prod DB in this review — but it is standard
Postgres behaviour and is exactly the divergence D3a was written about). So the guarantee AC22
makes for the *majority* of milestones is green on the only database the suite runs and unproven
on the one that matters.

**Fix:** state in the plan that there are two writers, and give the listener path its own
savepoint (the sync hook can call `session.begin_nested()`), or defer listener writes to a
point where the async recorder can be awaited. Then say plainly that no SQLite test can prove
it, and make the savepoint's presence the assertion instead.

---

### HIGH 5 — "Listeners registered once at app startup" is unreachable from the repo's HTTP tests [CODE-CONFIRMED]

`tests/conftest.py:174-181` builds the test client as `ASGITransport(app=app)` with no lifespan
manager, so **the app's startup never runs in tests**. If listener registration lives in the
lifespan (which "registered once at app startup" reads as), every route-level test — the whole
of slices 3, 5, 7 and 8 — runs with no listeners at all. The alternative, registering them by
hand in a fixture, proves the listeners work and never proves the production wiring exists.

Note also that a bare `db`-fixture test does not import `app.main`, so an import-time
registration in `app/identity/milestone_listeners.py` only takes effect if something imports
that module.

**Fix:** register at import of `app/models/__init__.py` (or another module the `db` fixture
already pulls in), and add a test that asserts registration happened *without* the test doing
the registering — e.g. `event.contains(...)` after importing only `app.models`.

---

### MEDIUM 6 — Plan test #6 asserts a parity the design deliberately breaks [CODE-CONFIRMED]

Test #6: *"Autopilot rows excluded in **both** live recording and backfill, so no step change at
the deploy date."*

`players.autopilot_at` is a **seat-level** column (`app/models/player.py:71`) and is stamped
mid-match when a human walks away (`app/routes/web_play.py:371-372`: *"in-match: seat auto-Hoards
to the end"*). So a user who genuinely played four turns and then left has:

- live recording → `played_turn` recorded at turn 1 (correct);
- backfill → the whole seat excluded, no `played_turn`.

Spec D4 accepts exactly this over-exclusion ("genuine turns played *before* a player went on
autopilot are dropped"). The two therefore *cannot* agree, and there *is* a step change at the
deploy date for that cohort. The test can only pass by picking a fixture where the player never
played before going on autopilot — a fixture chosen to dodge the assertion. Reword to what is
true: "autopilot-only seats are excluded by both; a seat that played and then left is counted
live and dropped by the backfill (D4's accepted over-exclusion)".

---

### MEDIUM 7 — The fixture sketch's four submission cases are at most two distinguishable row shapes [CODE-CONFIRMED]

The plan asks for "a match factory that can produce genuine, defaulted, autopilot, and
connector-fallback submissions — the four cases D5 distinguishes". At the data level they are
not four:

- `app/games/hoard_hurt_help/game.py:216-218` — *"Connector fallbacks reuse the existing
  `was_defaulted` column so they are identifiable in the DB without a migration."* A
  connector-fallback row is **byte-identical** to a missed deadline.
- autopilot is byte-identical to genuine; the difference lives on the *player* row.
- a NULL `submitted_at` row is only ever written by the resolver, never by a request.

Consequences for slice 3's must-prove ("autopilot, defaulted, connector-fallback and
null-timestamp rows record nothing"):

1. Connector-fallback can only be exercised by calling the live path with
   `is_connector_fallback=True` (`app/engine/player_move.py:30`, `app/engine/agent_play.py:104`)
   — a request-level harness, not a fixture.
2. The null-timestamp case is **vacuous** at the live hook: nothing ever calls the recorder
   there, so the test asserts a property of code that does not exist. Its real home is the
   slice-6 backfill, where the filter is actually written.

`tests/factories.py` gives you `add_submission(..., was_defaulted, submitted_at)` but nothing
for `autopilot_at` (no `seat_player` parameter for it) and nothing for connector-fallback. That
is the hidden work: a player-level autopilot knob, plus a live-path harness for the fallback
case.

---

### MEDIUM 8 — "Backfill and creation rule agree on one fixture set" cannot be one test [CODE-CONFIRMED]

The two live in different processes. `tests/test_migrations.py:33-46` runs Alembic in a
**subprocess** against a throwaway SQLite file, configured only through environment variables
(`test_0028_adds_user_roles_and_match_owner_column` uses
`monkeypatch.setenv("PLATFORM_ADMIN_EMAILS", ...)` for exactly this reason). The creation rule
runs in-process and is configured with `monkeypatch.setattr(settings, ...)`. Seeding is raw SQL
on one side and `tests/factories.py` on the other. There is no way to point both at one fixture
set; you get two fixture sets, which is the drift the requirement exists to prevent.

Good news: migrations **can** import app code (`migrations/versions/0028_user_roles.py:15`
imports `app.config.settings`), so the shared predicate is achievable. The honest test shape is:
unit-test `internal_accounts.is_internal_email` directly over a table of cases, and separately
assert the migration imports and calls it — not "run both against one fixture set".

---

### MEDIUM 9 — AC12's "survives the OAuth round trip" has no harness in this repo [CODE-CONFIRMED]

No test drives `/auth/google/callback`. `tests/test_auth.py` says it "mocks the Google OAuth
dance" and then just inserts `User` rows directly; every signed-in test in the suite forges the
cookie with `tests/conftest.py:signed_in_cookies`. The real callback calls
`oauth.google.authorize_access_token(request)` (`app/routes/auth.py:92`), which needs OAuth state
in the session.

So slice 5's must-prove "flag on ⇒ survives navigation + OAuth" needs new machinery
(monkeypatching the Authlib client on the app-global `oauth.google`, then driving
`GET /?utm_source=…` and `GET /auth/google/callback` on one cookie-carrying client). It is
writable, but it is a first for this repo and the plan budgets nothing for it. The nearby
shortcut — `/dev/login` — is the wrong path: `dev_login_available()` gates it behind
`DEV_LOGIN_ENABLED and not cookie_secure`, it needs `create_app()` to re-mount, and the plan has
dev-login users always flagged internal.

---

### MEDIUM 10 — Slice 5 breaks five existing test fakes and the plan never assigns that work [CODE-CONFIRMED]

`grep -c "async def fake_sync_google_user" tests/test_mcp.py` → **5**, each a two-parameter
`(db, userinfo)` fake monkeypatched over `mcp_server.connection_identity.sync_google_user`. A
third argument carrying first touch makes all five raise `TypeError` at call time. There are two
real callers to update as well (`mcp_server/connection_identity.py:176`,
`mcp_server/oauth_auth.py:156`) and ~12 direct positional call sites in
`tests/test_auth_user_sync.py` / `tests/test_account_disabled.py` (safe only if the new
parameter is keyword-with-default).

The spec lists this as build item 12. The plan's build order and slice-5 must-prove do not
mention it, while the gate is "each slice ends green (`ruff`, `mypy`, `pytest`)".

---

### MEDIUM 11 — The flag must be read per-request, or the flag-ON tests cannot run at all [CODE-CONFIRMED]

`app = create_app()` executes at import (`app/main.py`), and `create_app` adds every middleware
there. Monkeypatching `settings.first_touch_capture_enabled` in a test cannot undo an add-time
decision, and Starlette will not let you add middleware to a running app. The plan's prose
implies a per-request check ("the middleware returns immediately"), but never states it as a
constraint — and slice 5's only *named* flag test is the OFF case, which passes trivially under
the broken implementation too.

**Fix:** make "the flag is read inside `dispatch`, never at `add_middleware` time" an explicit
slice-5 requirement, and pair the OFF test with an ON test that flips the setting with
`monkeypatch.setattr` on the already-built app.

---

### LOW 12 — Slice 1's must-prove is wrong for one of its own columns [CODE-CONFIRMED]

"existing users read NULL" contradicts the plan's own data model, where `is_internal` is
`Boolean, nullable=False, server_default=false`. A test written to that sentence fails. The
correct assertion is the one `test_0047_adds_mutual_help_decay_default_on` already models:
insert a row *before* the migration, then assert the server default backfilled it to `false`.

---

### LOW 13 — The migration tests are in the fast lane, not the slow one [CODE-CONFIRMED]

`pytest tests/test_migrations.py -m "not integration" --collect-only` collects **all 28** tests:
the auto-tagger in `tests/conftest.py:52-68` keys off DB/HTTP fixtures, and these use `tmp_path`
+ subprocess instead. Measured cost of one two-step migration test: **~1.5 s**. Slices 1, 4 and 6
each add at least one, and slice 6's needs three chained upgrades over a large seed. "The fast
lane must stay green throughout" will still hold; "fast" will not — plan for the fast lane to
roughly double.

---

## Residual Risks

- **Slice-by-slice provability is otherwise sound.** Slices 1, 4, 7 and 8 are writable with
  existing patterns: `tests/test_migrations.py` for the migration round trip,
  `tests/test_admin_ui.py` (`client` + `reset_db` + `signed_in_cookies`) for the page and the
  toggle, and `app/deps.py:38-44` really does raise 401 for anonymous, so AC1's 401/403 split is
  testable as written. All five deletion sites in test #3 are callable async functions
  (`gc_pending_connections`, `release_held_seats`, `agents_lifecycle`, `match_deletion.delete_match`,
  `admin_user_actions.reset_handle`), so that test is writable. "No cookie was set" is a clean
  assertion: `grep -rn "set_cookie" app/` returns nothing, so `SessionMiddleware` is the only
  Set-Cookie source and it stays silent on an unmodified session.
- **`except IntegrityError: pass` will not trip the Preflight Gate.** I ran ruff with
  `--extend-select S110,S112` over the plan's exact recorder body: clean. Ruff's
  `check-typed-exception` defaults to false, so only bare/`Exception` catches are flagged. The
  plan's fail-open comment satisfies the constitution's carve-out. Not a finding — recorded so
  nobody "fixes" it later.
- **Blast radius on the existing suite is unmeasured.** With `after_flush_postexec`, an
  `after_insert` on `User`/`Agent`/`Player` fires inside `tests/factories.py` (`make_user`,
  `make_agent`, `seat_player` all flush), so **every** existing integration test starts writing
  `user_milestones` rows. Nothing in the plan estimates that. Query-count assertions are probably
  safe (`tests/test_read_models.py::_count_selects` counts SELECTs only, and the recorder issues
  an INSERT), but this should be checked at the end of slice 2 rather than discovered in slice 7.
- **`app/sqlite_parity.py` raises `StringLengthExceeded(ValueError)` in `before_flush`, which the
  recorder's `except (IntegrityError, SQLAlchemyError)` does not catch.** An over-long
  `agent_kind`/`source_match_id`/`milestone` would break the caller in tests while prod's
  `DataError` (a `SQLAlchemyError`) would be swallowed — the divergence runs the wrong way. Low
  probability with today's values (`ai`, `human`, `M_…`), but it is a hole in the fail-open
  contract that no planned test touches.
- **Milestones whose `source_match_id` points at a deleted match are unspecified for AC17.** The
  column has no FK, so the row survives a match delete; whether the smoke-test exclusion is an
  inner join (drops the milestone) or a left join (keeps it) changes the headline counts. Neither
  slice 7 nor any named test pins it.
- **Reviewer independence.** This is a Claude lens reviewing a Claude-authored plan, the same
  caveat the spec records for round 2. The findings above that were *executed* against the repo
  (3, 4, and the ruff/marker checks) do not depend on that judgement; the rest are reasoning over
  read code.

```json
{"reviewed": true, "findings": [{"severity": "HIGH", "title": "Seven acceptance criteria have no test anywhere in the plan", "detail": "AC1a, AC6, AC7, AC9, AC14, AC18 and AC19 appear in no slice must-prove and in none of the seven highest-value tests, so the spec's claim that every criterion has a matching test is false — and AC1a (summary numbers) and AC9 (internal-user exclusion on the page) are repeats of gaps rounds 2 and 3 already found."}, {"severity": "HIGH", "title": "The authoritative criteria list in state.json is stale", "detail": "state.json holds 22 revision-3 criteria with no AC0, AC1a or AC1b and still says '403 for a non-admin', so the plan's own deploy-gate test (flag off => no cookie) maps to no machine-readable criterion."}, {"severity": "HIGH", "title": "The planned after_commit listener write silently persists nothing", "detail": "Executed against this repo's SQLAlchemy 2.0.50 + aiosqlite: writing in after_commit raises InvalidRequestError inside the hook, the caller's commit still returns OK and no row is written, and slice 2's must-prove wording ('listeners fire') passes against exactly that broken shape — only after_flush_postexec persists."}, {"severity": "HIGH", "title": "The advisory-safety test covers only one of the two writers", "detail": "record_milestone is async and savepoint-wrapped so plan test #4 discriminates for explicit calls (verified: add-only fails with IntegrityError, add+flush with PendingRollbackError), but a sync flush-hook listener cannot await it, and a failed insert inside a flush hook leaves the SQLite caller healthy — so AC22 is unprovable for the three listener-driven milestones on the only database the suite runs."}, {"severity": "HIGH", "title": "'Listeners registered at app startup' is unreachable from the HTTP test harness", "detail": "tests/conftest.py builds the client as ASGITransport(app=app) with no lifespan manager, so app startup never runs in tests; lifespan registration would leave every route-level milestone test listener-less, and manual registration in a fixture never proves the production wiring."}, {"severity": "MEDIUM", "title": "Plan test #6 asserts an autopilot parity the design deliberately breaks", "detail": "players.autopilot_at is seat-level (app/models/player.py:71) and is stamped mid-match when a human leaves (app/routes/web_play.py:372), so a player who genuinely played and then walked away is recorded live but excluded by the backfill — the step change D4 explicitly accepts, which the test claims cannot happen."}, {"severity": "MEDIUM", "title": "The fixture sketch's four submission cases are only two distinguishable row shapes", "detail": "app/games/hoard_hurt_help/game.py reuses was_defaulted for connector fallbacks so they are byte-identical to missed deadlines, autopilot differs only on the player row, and a NULL submitted_at row is never produced by a request — so connector-fallback needs a live-path harness and the null-timestamp assertion is vacuous at the live hook."}, {"severity": "MEDIUM", "title": "'Backfill and creation rule agree on one fixture set' cannot be one test", "detail": "The backfill runs in an Alembic subprocess against a separate SQLite file configured only through env vars (tests/test_migrations.py:33-46), while the creation rule runs in-process with monkeypatch.setattr(settings, ...), so the achievable shape is a shared-predicate unit test plus an assertion that the migration calls it."}, {"severity": "MEDIUM", "title": "AC12's OAuth round trip has no harness in this repo", "detail": "No test drives /auth/google/callback — test_auth.py inserts User rows directly and every signed-in test forges the cookie via conftest.signed_in_cookies — so slice 5's 'survives OAuth' must-prove requires new Authlib monkeypatch machinery the plan does not budget."}, {"severity": "MEDIUM", "title": "Slice 5 breaks five existing sync_google_user fakes with no work assigned", "detail": "tests/test_mcp.py defines five two-parameter fake_sync_google_user fakes that will TypeError once the signature gains a first-touch argument, plus two real MCP callers; the spec lists this as build item 12 but the plan's build order and slice-5 must-prove never mention it, while every slice must end green."}, {"severity": "MEDIUM", "title": "The capture flag must be read per-request or the flag-ON tests cannot run", "detail": "app = create_app() runs at import and adds every middleware there, so monkeypatching settings cannot undo an add-time gate; the plan implies a per-request check but never states it, and its only named flag test is the OFF case, which also passes under the broken implementation."}, {"severity": "LOW", "title": "Slice 1's 'existing users read NULL' contradicts its own is_internal column", "detail": "is_internal is Boolean NOT NULL with server_default=false, so the correct assertion is the pre-existing-row backfill check that test_0047_adds_mutual_help_decay_default_on already models, not a NULL read."}, {"severity": "LOW", "title": "Migration tests run in the fast lane, so 'fast' roughly doubles", "detail": "All 28 tests in tests/test_migrations.py collect under -m 'not integration' because the auto-tagger keys off DB/HTTP fixtures and these use tmp_path plus subprocesses; one two-step migration test measured ~1.5s, and slices 1, 4 and 6 each add at least one."}]}
```

## Runner Stats
- total_input=0
- total_output=0
- total_tokens=0

## Resolution
- status: accepted
- note: 13 findings (5 HIGH). Also executed rather than argued. Independently confirmed the after_commit breakage and, decisively, that slice 2's own must-prove list would have PASSED against the broken shape - the checkpoint would have gone green while recording nothing, and slices 3-8 would have built on it. Slice 2's proof list now requires assertions from a fresh session after commit. Also accepted: seven ACs had no test anywhere (AC1a/6/7/9/14/18/19) and are now assigned to owning slices, with a note that the three summary numbers have lost their tests in three consecutive rounds - a drafting blind spot, not bad luck; listeners registered at app startup are unreachable under the test client's ASGITransport (no lifespan); the flag must be read per-request because create_app() runs at import; no test in the repo drives the OAuth callback, so that harness is new work in slice 5; connector-fallback rows are byte-identical to missed deadlines, so the fixture sketch's 'four cases' are two distinguishable shapes. Credit recorded: the savepoint recorder's safety test genuinely discriminates, and except IntegrityError: pass does not trip ruff S110.
