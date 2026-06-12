# Plan: Platform Admin User Management

**Slug:** admin-user-management
**Branch:** feat/admin-user-management
**Spec:** docs/workflow/feature-runs/admin-user-management/spec.md
**Reuse report:** docs/workflow/feature-runs/admin-user-management/reuse-report.md

## Review Reconciliation

- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: All findings are implementation-level (HOW), resolved in plan.md (not spec WHAT). HIGH content-negotiation: require_user is shared web+API — plan Decision 2 branches on Accept/path (303 web vs 403 JSON). HIGH N+1 on require_connection: plan specifies single query with join/select of owner disabled_at, no second SELECT. HIGH ConnectionSetup ordering: plan mandates disabled-owner check pre-empts the setup-token provisioning block. MEDIUM nav ghost-state: plan has get_current_user treat disabled as logged-out for nav. MEDIUM SQLite FOR UPDATE: plan Decision 5 corrected to dialect-branch (with_for_update only on non-sqlite). MEDIUM audit index: plan/migration adds index on target_user_id.
- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Implementation-level, resolved in plan.md. MEDIUM admin JSON APIs + 303: same content-negotiation fix (plan Decision 2) covers admin_api/game_admin_api. MEDIUM login ordering: plan specifies disabled check happens before establishing the session (sync is additive/harmless but no session is set for disabled users). LOW ADMIN_EMAILS fallback: plan clarifies the floor = resolved platform_admin_emails_set (includes legacy fallback) and FR-017 warns on the RESOLVED set being empty.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: HIGH#1 revocation gap: documented 2-step process (remove config + second admin demotes); additive sync is intentional for durability. HIGH#2 load_only: T2.3 updated to use .load_only(User.disabled_at). HIGH#3 content negotiation: rejected — plan T2.1 'else → 403 JSON' already covers Accept:*/* correctly; 303 only on explicit Accept:text/html. MEDIUM#4 Jinja guard: T2.6 updated with {% if user and user.disabled_at %}. MEDIUM#5 actor index: T1.2/T1.4 updated to add actor_user_id index. LOW#6 db.get_bind(): plan is correct — AsyncSession proxies get_bind() to sync session; db.bind was removed in SQLAlchemy 2.x so Gemini's suggestion is invalid for our version.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: HIGH#1 Connection.user relationship: added T1.3b to tasks. MEDIUM#2 __init__.py: already covered by T1.3. MEDIUM#3 nav_context CTA: T2.6 updated to hide CTA/counts in disabled nav state. Residual risks noted; next_after_login cleared by redirect in auth callback.

## Overview

The role foundation already exists (`User.role`, `require_platform_admin`,
migration `0028`). This feature adds: a `disabled_at` column + two-path disable
enforcement, in-app promote/demote endpoints with an additive login-sync fix, an
`AdminAuditLog` table, and the `/admin/users` list + detail pages. Most code
mirrors existing patterns (see Reuse Addressing); the only net-new building
blocks are the audit model and the offset+ilike list query.

## Architecture Decisions

### Decision 1: New `AdminAuditLog` table, not extending `RequestIncident`
`RequestIncident` is an error/500 capture log (no actor/target/action, bare
`user_id`, no FK). Admin actions need actor vs target, an action enum, an optional
reason, and `ON DELETE RESTRICT`. Build a small purpose-built table; reuse only
the *conventions* (`created_at` server-default, `FlexibleEnumType` for the action
enum, newest-first `order_by(...).limit(...)` read).

### Decision 2: Two-path disable enforcement, content-negotiated
`get_user_from_session` stays a pure getter (`-> User | None`). The disabled
checks live in the dependencies:
- `require_user` is shared by **both** web pages and JSON APIs (`admin_api.py`,
  `game_admin_api.py`, agent web). So the disabled check there MUST
  content-negotiate, not blanket-303 (resolves review HIGH: 303 would break JSON
  clients that follow it and try to parse HTML). Rule (three branches, in
  `raise_account_disabled(request)` in `app/auth/session.py`, reused by both deps):
  (1) **HTMX request** (`HX-Request` header) → return **200 with
  `HX-Redirect: /disabled`** so HTMX does a full-page navigation instead of
  swapping `/disabled` into a fragment (resolves review HIGH: HTMX would break the
  layout); (2) **plain browser HTML** (`Accept: text/html`, no `HX-Request`) →
  raise **303 → `/disabled`**; (3) **JSON/API** → raise **403 JSON
  `ACCOUNT_DISABLED`** (same envelope as `NOT_SIGNED_IN`).
- `require_connection` (bot/runner) → always raise **403 JSON `ACCOUNT_DISABLED`**
  (mirror the `CONNECTION_PAUSED` block at `app/deps.py:226`).
This is the fix for the disable-bypass HIGH finding. A disabled DB-admin is
blocked here *before* any role check runs.

### Decision 3: Additive login-sync
`sync_google_user` changes from "set role from config every login" to: if email
∈ `platform_admin_emails_set` → ensure `ADMIN`; else **leave `user.role`
unchanged**. This makes in-app promotions durable. New users still default to
`USER` via the model default. (Revocation of a config admin is the documented
two-step: remove from config, then demote in-app.)

### Decision 4: Immutable floor check is case-insensitive (full immutability)
The config-floor refusal (FR-010) compares `target.email.lower()` against
`platform_admin_emails_set` (already lowercased in `config.py`). Same `.lower()`
convention already used by `require_game_admin` and `sync_google_user`.
**Config admins are FULLY immutable in-app — neither demote nor disable** (user
decision, reconfirmed after the plan review raised an emergency-disable
carve-out; the carve-out was explicitly rejected). The emergency path for a
compromised config admin stays: revoke Google OAuth + remove from
`PLATFORM_ADMIN_EMAILS` + redeploy. The floor check applies to BOTH the demote
and disable actions in `admin_user_actions.py`.

### Decision 5: Mutations lock the target row (dialect-aware) + single transaction
Each disable/enable/promote/demote loads the target, re-checks current state for
the no-op rule, mutates, writes the audit row, and commits — all in one
transaction. Row locking is **dialect-branched**: SQLite does NOT support
`SELECT ... FOR UPDATE` row locks (it would error or lock the whole file —
resolves review MEDIUM), so apply `.with_for_update()` only on Postgres. Use the
async-safe dialect accessor — `db.get_bind().dialect.name` (NOT `db.bind`, which
isn't reliably exposed on an `AsyncSession` and risks a 500 — resolves review
LOW): `if db.get_bind().dialect.name != "sqlite": stmt = stmt.with_for_update()`.
On SQLite
(test DB) the connection-level write serialization already prevents the race, so
SC-004 still holds. Helper lives in `app/services/admin_user_actions.py`.

### Decision 6: Reuse the admin page/router pattern
New routes live in `admin_web.py` (web) and `admin_api.py` (any JSON actions)
under `require_platform_admin`. Templates go in `templates/admin/`. The "Users"
nav link reuses the existing `{% if is_admin %}` block in `base.html`.

### Decision 7: `require_connection` — single query, check before provisioning
Two correctness/perf points from the plan review on the platform's hottest path:
- **No N+1 on the live path (review HIGH)**: do not add a second `SELECT` for the
  owner. Extend the existing `Connection` lookup with
  `select(Connection).options(joinedload(Connection.user))` — this returns a
  **`Connection` scalar** (with `connection.user` populated) in one round trip.
  Do NOT use `select(Connection, User.disabled_at)` — that returns a `Row` tuple
  and would break every `require_connection` caller (`agent_api.py`,
  `agent_next_turn.py`) that expects a `Connection` (resolves review HIGH: tuple
  return). Check `connection.user.disabled_at` where `deleted_at`/`PAUSED` are
  checked.
- **Setup branch needs one targeted lookup (review MEDIUM, clarified)**: the
  `ConnectionSetup` provisioning branch has **no `Connection` yet**, so the
  joinedload form can't apply. Here a single `select(User.disabled_at).where(
  User.id == setup.user_id)` is required and acceptable — this is the rare
  one-time token-redemption path, not the per-turn hot path, so it is not the
  N+1 the review warned about. Run it BEFORE constructing the `Connection` /
  `db.flush()` (the setup block at `app/deps.py:198-219`, fed by
  `connections_setup.py`); raise 403 `ACCOUNT_DISABLED` if disabled — so a
  disabled user cannot consume a setup token or create a new `Connection` row.

### Decision 8: Disabled users keep a session; route to `/disabled` (no login loop)
The earlier draft (callback issues no session + `get_current_user` returns `None`)
was self-contradictory — it would cause an **infinite login loop**: an anonymous
browser hitting a protected route looks "logged out," so `require_user` bounces it
to Google sign-in, never reaching `/disabled` (review HIGH, both reviewers).
Corrected model:
- **Login DOES set a session.** The Google callback runs `sync_google_user` and
  establishes the session as normal. If the user is disabled, it then redirects to
  `/disabled` — but the session cookie exists, so the user is identifiable.
- **`get_current_user` returns the `User`** (disabled or not) — it does NOT fake
  anonymity. Nav/templates check `{% if user.disabled_at %}` to hide sensitive
  links and show an "account disabled" state instead of normal nav (resolves the
  ghost-state concern without the loop).
- **`require_user` is the gate.** It detects `disabled_at` and calls
  `raise_account_disabled(request)` (Decision 2) — so every hard-auth route (web,
  API, HTMX) routes a disabled user to the correct `/disabled` response.
- **Soft-auth viewing routes** that call `get_current_user` directly
  (`web_lobby.py`, `web_player.py`) still render public/spectator content for a
  disabled user (viewing is allowed); any *action* on those pages goes through
  `require_user`/`require_connection` and is blocked. This avoids the Codex
  finding that returning `None` would shove disabled users into the login/handle
  flow.

### Decision 9: Admin floor = resolved set; audit index
- **Legacy fallback (review LOW)**: the immutable floor is the **resolved**
  `platform_admin_emails_set`, which still falls back to `ADMIN_EMAILS` during the
  compat window. FR-017's empty-floor warning fires on the **resolved** set being
  empty (so a lingering `ADMIN_EMAILS` correctly counts as a non-empty floor).
- **Audit index (review MEDIUM)**: migration `0029` adds an index on
  `admin_audit_log.target_user_id` so the detail-page query
  (`WHERE target_user_id = ?`) does not sequential-scan as the log grows.

## Files Changed

| File | Change |
|---|---|
| `app/models/user.py` | Add `disabled_at: Mapped[datetime \| None]` |
| `app/models/admin_audit_log.py` (NEW) | `AdminAuditLog` model + `AdminAction` enum |
| `app/auth/session.py` | Add `raise_account_disabled(request)` content-negotiated helper (303 HTML / 403 JSON) |
| `app/deps.py` | Disabled checks in `require_user` (content-negotiated `raise_account_disabled`) and `require_connection` (403 `ACCOUNT_DISABLED`, joinedload owner + pre-provisioning lookup). `get_current_user` keeps returning the `User` (incl. disabled) — nav branches on `user.disabled_at`; it does NOT return `None` for disabled (would cause a login loop) |
| `app/routes/auth.py` | Make `sync_google_user` additive; Google callback sets the session then redirects a disabled user to `/disabled` |
| `app/templates/base.html` (nav) | Show an "account disabled" state when `user.disabled_at` is set instead of normal links |
| `app/routes/admin_web.py` | `/admin/users` list, `/admin/users/{id}` detail, disable/enable + promote/demote handlers, audited handle-reset, badges on `/admin/handles` |
| `app/routes/web_lobby.py` (or nearest public web router) | Public `GET /disabled` notice |
| `app/services/admin_user_actions.py` (NEW) | Shared mutation helpers (floor check, row-lock, audit write) used by the handlers |
| `app/templates/admin/users_list.html` (NEW) | User list page |
| `app/templates/admin/user_detail.html` (NEW) | User detail page |
| `app/templates/disabled.html` (NEW) | Disabled-account notice |
| `app/templates/admin/handles.html` | Add disabled/admin badges |
| `app/templates/base.html` | Add "Users" admin nav link |
| `app/main.py` | Startup warning when `platform_admin_emails_set` is empty |
| `migrations/versions/0029_admin_user_management.py` (NEW) | Add `users.disabled_at` + `admin_audit_log` table |
| `tests/...` | New tests per slice (see below) |

## Reuse Addressing (every reuse-report row)

- **List pagination/search** → new query (`.offset()`/`ilike` exist nowhere);
  page pattern extends `admin_handles`. Justified-new query, reused page shell.
  The `q` filter searches **`email` OR `handle`**, case-insensitive substring
  (`ilike('%' || q || '%')` on both, OR-combined) — resolves review LOW on
  ambiguous search scope.
- **Audit log** → justified-new table (Decision 1).
- **Connection-path disable** → extend `require_connection` (`CONNECTION_PAUSED`).
- **Web-path disable** → extend `require_user`; mirror the `require_user_with_handle` 303.
- **Promote/demote** → new endpoints; extend `sync_google_user` (Decision 3).
- **`/disabled` route** → reuse a public GET pattern from `web_lobby.py`.
- **Admin nav + templates** → reuse `admin_web.py` + `templates/admin/` + `base.html` is_admin.
- **Migration** → reuse `0028_user_roles.py` batch_alter_table template; head = `0028`.
- **Startup warning** → extend `_check_oauth_config()` (`main.py`), called from lifespan.

## Implementation Order (checkpoint-bounded slices)

### Slice 1 — Data model + migration `[CHECKPOINT]`
`User.disabled_at`, `AdminAuditLog` model + `AdminAction` enum, migration `0029`
(batch mode) **including an index on `admin_audit_log.target_user_id`** (Decision
9). Tests: model imports, migration applies on SQLite test DB
(`tests/test_migrations.py` style), audit FK is `RESTRICT`, index present.
~130 lines.

### Slice 2 — Disable enforcement (both auth paths) + additive login-sync `[CHECKPOINT]`
Content-negotiated `raise_account_disabled` helper (HX-Redirect / 303 / 403 JSON —
Decision 2); `require_user` calls it for disabled users; `require_connection` 403
`ACCOUNT_DISABLED` via `joinedload(Connection.user)` on the live path + a single
targeted `User.disabled_at` lookup that pre-empts setup provisioning (Decision 7);
`get_current_user` keeps returning the `User` (nav branches on `user.disabled_at`
— NOT `None`, Decision 8); public `/disabled` route + template; Google callback
sets the session then redirects a disabled user to `/disabled` (Decision 8);
`sync_google_user` additive (Decision 3).
Tests: disabled user gets **303 on a browser web route**, **HX-Redirect on an HTMX
request**, and **403 JSON on an API route**; disabled owner's connection key gets
403 `ACCOUNT_DISABLED` on `next_turn`/`next_turns`/`report_pid`; disabled owner
cannot consume a `ConnectionSetup` token (no new Connection row created); a
disabled-but-signed-in user reaches `/disabled` (NO login loop) and
`get_current_user` returns the disabled user; login callback DOES set a session
then lands on `/disabled`; promoted-in-app user keeps `ADMIN` across a re-login;
config email still forced to `ADMIN`. ~240 lines.

### Slice 3 — Mutation service + endpoints (disable/enable/promote/demote) `[CHECKPOINT]`
`admin_user_actions.py` (floor check, row-lock, no-op rule, audit write); wire
handlers in `admin_web.py`; retrofit `admin_reset_handle` through the audit path
(no-op when no handle). Tests: each action writes exactly one audit row; no-op
writes none; floor email refused (case-insensitive); non-admin → 403. ~220 lines.

### Slice 4 — Admin UI (list + detail + badges + nav) `[CHECKPOINT]`
`/admin/users` (paginated, `q` search), `/admin/users/{id}` (bounded recent
matches + audit history), badges on `/admin/handles`, "Users" nav link. Tests:
list renders + search filters; detail shows connections/agents/audit; non-admin
→ 403. ~200 lines.

### Slice 5 — Startup warning + docs/STATUS `[CHECKPOINT]`
Empty-`PLATFORM_ADMIN_EMAILS` warning in `main.py` lifespan; STATUS.md update.
Test: warning fires when set is empty. ~40 lines.

## Risks and Mitigations

- **Risk: additive login-sync silently keeps a removed config admin as ADMIN.**
  verification: a unit test sets a user to `ADMIN`, removes their email from the
  config set, runs `sync_google_user`, and asserts role stays `ADMIN` (documented
  two-step) AND that a config-listed email is still forced to `ADMIN`.
- **Risk: connection-path check missed on a code path, leaving disable bypassable.**
  verification: integration test disables a user, then hits `next_turn` /
  `next_turns` / `report_pid` with that user's connection key and asserts 403
  `ACCOUNT_DISABLED` on each.
- **Risk: 303 redirect injected into a shared primitive breaks API clients.**
  verification: test that the connection/API path returns structured JSON (not an
  HTML redirect) for a disabled user, and a web route returns 303→`/disabled`.
- **Risk: migration fails on SQLite (batch mode).**
  verification: `tests/test_migrations.py` upgrades head on the in-memory SQLite
  DB; `alembic upgrade head` clean.
- **Risk: duplicate audit rows under concurrent admins.**
  verification: test the no-op rule (re-disabling an already-disabled user writes
  no second row); row-lock acquired in the mutation helper.
- **Residual: orphaned match queue for a disabled spammer.** Documented non-goal;
  follow-up bulk match-cancel. verification: n/a (explicitly out of scope; noted
  in closeout).

## Verification Checklist (from spec acceptance criteria)

- AC list/search, AC detail page, AC disable→reject-next-request (both paths),
  AC re-enable, AC promote/demote + survives re-login, AC floor refusal,
  AC one-audit-row-per-action, AC non-admin 403, AC migration applies.
- Preflight Gate: `ruff check .`, `mypy app/ mcp_server/`, `pytest -q` all green.
