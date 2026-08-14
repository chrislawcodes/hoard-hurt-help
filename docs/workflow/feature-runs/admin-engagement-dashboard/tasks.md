# Tasks — Admin Engagement Dashboard + Signup-Source Capture

Derived from `plan.md` revision 3. Eight slices; a `[CHECKPOINT]` at each slice
boundary. Every checkpoint requires `ruff` + `mypy` + `pytest` green from the repo
root before it is taken.

**Read before starting any slice:** the plan's "What was wrong, so nobody
re-derives it" table. Three separate mechanism shapes were tried and refuted by
execution; the table exists so they are not re-attempted.

---

## Slice 1 — Models and schema migration

- [ ] `app/models/user_milestone.py` — `UserMilestone` with `user_id` (FK,
      `ondelete="CASCADE"`, indexed), `milestone` (`String(32)`,
      `FlexibleEnumType`), `reached_at` (`DateTime(timezone=True)`),
      `source_match_id` (`String(32)`, nullable).
      `UniqueConstraint(user_id, milestone)`; index on `(milestone, reached_at)`.
- [ ] `MilestoneKind` enum: `signed_up`, `picked_handle`, `set_up_ai_agent`,
      `set_up_human_play`, `ai_connected`, `joined_match`, `played_turn`.
      **No `returned`** — derived at read time.
- [ ] **`app/models/__init__.py` — import and export `UserMilestone`.** This is
      what puts the model in `Base.metadata`, which every test schema is built
      from. Omit it and the suite goes green against a schema with no table.
- [ ] `app/models/user.py` — five `first_utm_*` / `first_referrer_host` /
      `first_landing_path` columns, `first_source_channel(16)`, and
      `is_internal` (`Boolean`, `nullable=False`, `server_default="false"`).
- [ ] Migration: additive only, `op.batch_alter_table` for any constraint work.
      No backfill in this slice.
- [ ] Tests: `upgrade`→`downgrade` clean on SQLite; the table exists in a
      `create_all` schema; existing users read NULL sources and
      `is_internal=False`.

`[CHECKPOINT] slice-1-models`

---

## Slice 2 — The recorder and its listeners

- [ ] `app/identity/milestones.py`:
      - `_record_sync(session, rows)` — `session.connection()` +
        `conn.begin_nested()` + Core `insert()`; `IntegrityError` caught **inside**
        the savepoint means "already recorded"; `SQLAlchemyError` logged.
      - `async def record_milestone(...)` — thin wrapper for the explicit call
        sites, which run in normal async context.
- [ ] `app/identity/milestone_listeners.py`, registered **at import**:
      - `after_insert` on **`User`, `Agent`, `Player`** — these are **mapper**
        events. Binding them to `Session` is accepted silently and fires zero
        times. Collect into `session.info`.
      - `after_flush_postexec` on **`Session`** — writes the collected rows, then
        **clears the collection**.
- [ ] `Agent` mapping: `kind='ai'` → `set_up_ai_agent`; `kind='human'` →
      `set_up_human_play`; `kind='bot'` → nothing.
- [ ] `Player` mapping: `joined_match` with `source_match_id`, **excluding the
      house bots user**.
- [ ] No re-entrancy guard — measured unnecessary, and a guard here is a
      silent-drop risk.
- [ ] Tests:
      - idempotent — recording twice leaves one row.
      - a forced `IntegrityError` leaves the caller's transaction usable and the
        caller's own rows still commit.
      - listeners fire for all three models **under the conftest fixtures**, not
        only under app startup.
      - **commit-ordering spy**: assert the milestone insert happens inside a
        transaction that is subsequently committed. A plain "read it back from a
        fresh session" assertion **cannot fail** here — the in-memory engine uses
        `StaticPool`, so every session shares one connection and a writer that
        never commits still reads back. This test is the slice's real gate.
      - composes with the existing savepoint at `mcp_connection.py:267`.

`[CHECKPOINT] slice-2-recorder`

---

## Slice 3 — The explicit recorders

- [ ] `picked_handle` — `handle_web.py`, `dev_login.py`.
- [ ] `ai_connected` — `connection_activity.mark_seen` and the four
      `mcp_connection.py` branches (145, 174, 206, 222).
- [ ] `played_turn` — `agent_play.submit_action:269` (genuine AI) and
      `web_play.py:253` (human). Not `record_submission`: it has five call sites
      and serves the defaulter too.
- [ ] Tests:
      - **human path records `played_turn` and feeds `returned`** (AC4).
      - **MCP user connecting before building an agent records both** (AC5).
      - defaulted, NULL-timestamp, autopilot and connector-fallback submissions
        record nothing (AC8).
      - bot seats record no `joined_match`.

`[CHECKPOINT] slice-3-explicit-recorders`

---

## Slice 4 — Internal accounts

- [ ] `app/identity/internal_accounts.py` — one predicate, used by both the
      creation rule and the migration backfill.
- [ ] `app/config.py` — `INTERNAL_EMAIL_DOMAINS`.
- [ ] Set `is_internal` at all three creation sites: `auth.py:41` (domain rule),
      `bots/seating.py:51` (always), `dev_login.py:51` (always).
- [ ] Migration backfill + `scripts/preview_internal_backfill.py`, read-only,
      following `scripts/preview_match_id_migration.py`'s `--db copy.db
      --dry-run` interface.
- [ ] Tests: survives an email rewrite; survives promote then demote; backfill and
      creation rule agree on one fixture set (asserted against the shared
      predicate, since the migration runs in a subprocess).

`[CHECKPOINT] slice-4-internal-accounts`

---

## Slice 5 — First-touch capture, behind the flag

- [ ] `app/config.py` — `FIRST_TOUCH_CAPTURE_ENABLED`, default `False`, read
      **per request** (`create_app()` runs at import, so an import-time read makes
      the flag-on tests unrunnable).
- [ ] `app/identity/first_touch.py` — middleware; length-cap **at capture**, before
      the value enters the signed cookie; skip `/static`, `/healthz`, `/api`,
      `/mcp`, `/openapi.json`, `/.well-known`, `/auth`; ignore an internal
      referrer; write `first_source_channel="direct"` when capture ran and found
      nothing.
- [ ] `app/main.py` — register the middleware **before** the `SessionMiddleware`
      call, so it runs inside it. The spec calls this the most likely silent
      failure in the feature.
- [ ] `app/auth/session.py` — `clear_session` also clears `first_touch`.
- [ ] `sync_google_user` — optional first-touch argument, written only on the
      new-user branch. Update its **three production callers** and its **14 test
      call sites** (`test_mcp.py`, `test_auth_user_sync.py`,
      `test_account_disabled.py`).
- [ ] MCP callers record `first_source_channel="mcp"`.
- [ ] Build the OAuth-callback test harness — **no test in this repo currently
      drives `/auth/google/callback`**. New work, not an assumption.
- [ ] Tests: **flag off ⇒ no cookie, nothing stored** (AC0); middleware ordering
      asserted; capture survives navigation + the OAuth round trip; no overwrite;
      truncation at capture; cleared on sign-out; MCP channel recorded.

`[CHECKPOINT] slice-5-first-touch`

---

## Slice 6 — Milestone backfill

- [ ] `scripts/preview_milestone_backfill.py` — read-only, `--db copy.db
      --dry-run`, reporting per-milestone row counts.
- [ ] **Write down how to obtain a production copy.** Today the only documented
      access is `DATABASE_PUBLIC_URL`, which is the *live* database. A dry run
      nobody knows how to produce will be skipped.
- [ ] Migration backfill: reconstruct each milestone from surviving rows;
      **exclude autopilot** via `players.autopilot_at`, or backfilled
      `played_turn` uses a looser rule than live recording and the page shows an
      unexplained step change at the deploy date.
- [ ] Tests: backfill output matches a fixture; autopilot excluded;
      `upgrade`→`downgrade` clean.

`[CHECKPOINT] slice-6-backfill`

---

## Slice 7 — Read models

- [ ] `app/read_models/engagement_milestones.py` — per-milestone counts over a
      signup cohort; **independent counts, no nesting**; `ai_connected` reported
      against `set_up_ai_agent` holders (AC7); `returned` derived from
      `turn_submissions` in the window timezone; smoke-test matches excluded;
      internal users excluded (**depends on slice 4**).
- [ ] Stuck list — furthest milestone, the two setup milestones as one rung,
      handle-less users labelled by email, capped at 50 with a remainder count.
- [ ] `app/read_models/signup_sources.py` — source → signups → played, distinct
      users; `NULL` renders `"unknown"`, explicit `"direct"` renders `"direct"`.
- [ ] Tests: AC6 independence, AC7 denominator, AC9 exclusion, AC14 unknown-vs-
      direct, AC15 distinct users, AC16 timezone (including a US evening spanning
      two UTC days), AC17 smoke tests, AC18 stuck list, empty DB renders.

`[CHECKPOINT] slice-7-read-models`

---

## Slice 8 — Page, navigation, admin toggle

- [ ] `app/routes/admin_engagement.py` + `app/templates/admin/engagement.html`.
- [ ] Three summary numbers, each stating window, population, and internal
      filtering (AC1a). Shares and period comparisons **suppressed below 20
      users** (AC1b) — test at n=19, n=20 and n=21.
- [ ] Both explanatory notes render (AC19): reconstructed history, and MCP
      attribution.
- [ ] `app/services/admin_user_actions.py` — the `is_internal` toggle, following
      the existing lock / no-op-guard / audit contract.
- [ ] `AdminAction` — add `mark_internal` and `unmark_internal` (paired, like
      every other reversible action).
- [ ] `user_detail.html` — the toggle, **outside** the `{% if not floor_admin %}`
      block; `users_list.html` — new column, header row **and** empty-state
      `colspan` 7→8; `base.html` and `admin/dashboard.html` — both nav surfaces.
- [ ] Tests: 401 anonymous / 403 signed-in non-admin; toggle both ways with an
      audit row; renders for a floor-admin target; summary numbers; both notes.

`[CHECKPOINT] slice-8-page`

---

## Before the PR

- [ ] Full Preflight Gate from the repo root.
- [ ] **Both backfill previews run against a production copy**, with row counts
      recorded in the PR body. These migrations run automatically on merge
      (`railway.json` `preDeployCommand`) and are irreversible in practice.
- [ ] PR body carries a `Validation` section with exact commands and results.
- [ ] **Raise the privacy obligation explicitly** (spec D8): this ships a
      persistent 14-day tracking cookie to a site with no privacy policy. The
      flag defaults off, so merging is safe — turning it on is Chris's separate
      decision.
