# Spec: Platform Admin User Management

**Slug:** admin-user-management
**Branch:** feat/admin-user-management

## Background

Platform admins (today: Chris-as-operator) have no in-app way to manage the
people on the platform. They can reset handles and view incidents from `/admin`,
but there is no user list, no way to stop a misbehaving user, and no way to grant
another person admin access without editing the `PLATFORM_ADMIN_EMAILS`
environment variable and redeploying.

This feature adds an in-app **user management** surface for platform admins:
view/search all users, **disable** (not delete) a user, and **promote/demote**
other platform admins using the existing DB role column. Every such action is
recorded to a persistent audit log.

## Current State (what already exists — reuse, do not rebuild)

The `admin-role-split` feature already shipped the foundation:

- **`User.role`** (`app/models/user.py`) — a `UserRole` enum (`ADMIN` / `USER`),
  stored via `FlexibleEnumType`, default `USER`. Migration `0028_user_roles`
  added it. **This is the role column decision #2 calls for — it already exists.**
- **`require_platform_admin`** (`app/deps.py:71`) already gates on
  `user.role == UserRole.ADMIN`. Platform-admin access is already DB-backed.
- **`sync_google_user`** (`app/routes/auth.py:23`) seeds `role` on every login:
  `ADMIN` if the email is in `settings.platform_admin_emails_set`, else `USER`.
- **`platform_admin_emails_set`** (`app/config.py:99`) — the configured admin
  email allowlist. This feature keeps it as the **immutable bootstrap floor**.
- Admin nav link is already conditional on `is_admin` in
  `app/templates/base.html`; existing platform-admin pages live under
  `app/routes/admin_web.py` + `templates/admin/`.

What is **missing** and this feature adds:

1. A user disable mechanism (new column + login/session enforcement).
2. In-app endpoints + UI to flip `role` (promote/demote) and disable/enable.
3. The login-sync clobber fix so an in-app promotion survives the next login.
4. The `/admin/users` list + `/admin/users/{id}` detail pages.
5. A persistent admin audit log.

## User Scenarios & Testing

### User Story 1 — See and find users (Priority: P1)

As a platform admin, I open `/admin/users` and see a searchable, paginated list
of everyone on the platform, so I know who is here and can find one person fast.

**Why P1**: Without a list there is nothing to manage. This is the entry point
and is independently valuable (read-only visibility) even before any action.

**Independent Test**: Sign in as a platform admin, visit `/admin/users`, confirm
the list shows users with email, handle, signup date, agent count, admin badge,
and disabled badge; type a query and confirm filtering by email/handle.

**Acceptance Scenarios**:
1. **Given** I am a platform admin, **When** I visit `/admin/users`, **Then** I
   see a paginated list of all users with email, handle, created date, number of
   agents, admin status, and disabled status.
2. **Given** the list, **When** I search "alice", **Then** only users whose email
   or handle contains "alice" are shown.
3. **Given** I am not an admin, **When** I request `/admin/users`, **Then** I get
   403.

### User Story 2 — Inspect one user (Priority: P1)

As a platform admin, I open `/admin/users/{id}` and see what that user owns
(connections, agents, recent matches) plus their current status, so I have the
context to decide on an action.

**Why P1**: Actions (disable, promote) should be taken from an informed detail
view, not blind from a list row.

**Independent Test**: Visit `/admin/users/{id}` for a user with agents and a
connection; confirm those are listed with their status, and the user's audit
history is shown.

**Acceptance Scenarios**:
1. **Given** a user with agents and connections, **When** I open their detail
   page, **Then** I see their connections (provider, status), agents (name,
   status), recent match participation, role, and disabled state.
2. **Given** the detail page, **When** prior admin actions exist for this user,
   **Then** I see the audit history (who did what, when).

### User Story 3 — Disable / re-enable a user (Priority: P1)

As a platform admin, I can disable a user so they can no longer sign in or act,
and re-enable them later, without destroying any of their data.

**Why P1**: This is the core "stop a bad actor" capability and the headline
decision (#1 disable-not-delete, #3 enforce at login).

**Independent Test**: Disable a user, confirm they cannot pass auth on their next
request and a fresh login is rejected; re-enable and confirm access returns.

**Acceptance Scenarios**:
1. **Given** an active user, **When** I disable them, **Then** `disabled_at` is
   set, an audit row is written, and the list/detail shows them as disabled.
2. **Given** a disabled user with a live session cookie, **When** they make their
   next authenticated request, **Then** they are rejected (303 to a "your account
   is disabled" notice), not served the page.
3. **Given** a disabled user, **When** they attempt a fresh Google login, **Then**
   the callback refuses to establish a session.
4. **Given** a disabled user, **When** I re-enable them, **Then** `disabled_at`
   clears, an audit row is written, and normal access returns.
5. **Given** disable/enable, **When** it completes, **Then** none of the user's
   connections, agents, or match history are deleted.

### User Story 4 — Promote / demote platform admins (Priority: P2)

As a platform admin, I can promote a regular user to platform admin and demote a
DB-promoted admin back to a regular user, in-app, without editing env vars.

**Why P2**: High value (no redeploy to add an admin) but the platform still
functions with only config-based admins, so it ranks below the core view/disable
loop.

**Independent Test**: Promote a non-admin user, confirm `role == ADMIN` and that
they can reach `/admin` on their next request; demote them, confirm access is
revoked. Log the promoted user out and back in; confirm they are **still** admin
(promotion survives login-sync).

**Acceptance Scenarios**:
1. **Given** a non-admin user, **When** I promote them, **Then** `role` becomes
   `ADMIN`, an audit row is written, and they can reach platform-admin pages on
   their next request.
2. **Given** a DB-promoted admin, **When** I demote them, **Then** `role` becomes
   `USER` and platform-admin access is revoked on their next request.
3. **Given** a user promoted in-app, **When** they log out and back in via Google,
   **Then** they remain `ADMIN` (login-sync does not reset them to `USER`).
4. **Given** a user whose email is in `PLATFORM_ADMIN_EMAILS` (a config/floor
   admin), **When** I try to demote or disable them, **Then** the action is
   refused with a clear message and no change is made.

### User Story 5 — Audit trail of admin actions (Priority: P2)

As a platform admin, every disable/enable/promote/demote/handle-reset action is
recorded so I can later answer "who did this to whom, and when."

**Why P2**: Accountability for privilege changes. The actions work without it, but
for a privilege surface it should ship together.

**Independent Test**: Perform each action type; confirm one audit row per action
with actor, target, action, timestamp, and optional reason; confirm rows render
on the user detail page.

**Acceptance Scenarios**:
1. **Given** any admin action in this feature, **When** it succeeds, **Then** a
   row is written with `actor_user_id`, `target_user_id`, `action`, optional
   `reason`, and `created_at`.
2. **Given** audit rows exist, **When** I view a user's detail page, **Then** the
   rows for that target are listed newest-first.

## Edge Cases

- **Login-sync clobber (critical)**: today `sync_google_user` sets
  `user.role = role` on every login from the config list, which would silently
  reset an in-app promotion back to `USER`. → Login-sync becomes **additive**:
  if the email is in `platform_admin_emails_set`, ensure `role = ADMIN`
  (floor); otherwise **leave the existing role unchanged**. Never force `USER`.
- **Admin revocation model (resolves review finding G-5)**: because login-sync
  is additive, removing someone from `PLATFORM_ADMIN_EMAILS` alone does **not**
  demote them — it only *lifts the immutable floor*. Revoking a config admin is a
  deliberate **two-step**: remove their email from `PLATFORM_ADMIN_EMAILS`
  (so the floor no longer protects them), then **demote them in-app**. This is
  the intended, documented behavior, not a bug. (A future enhancement could add
  an "admin source" marker so config removal auto-demotes config-sourced admins
  while preserving in-app promotions; out of scope here.)
- **Empty bootstrap list**: if `PLATFORM_ADMIN_EMAILS` is empty there is no
  immutable floor and a self-demotion could leave zero admins. → A non-empty
  bootstrap list is a deployment requirement. The app MUST log a loud startup
  warning when the set is empty (advisory only; does not block boot). Not
  otherwise enforced programmatically.
- **Compromised config admin (resolves review finding G-6)**: the immutable
  floor means a config admin cannot be disabled in-app. If such an account is
  compromised, the emergency path is: revoke the account's Google OAuth access
  and remove the email from `PLATFORM_ADMIN_EMAILS` (then redeploy). This latency
  is an accepted tradeoff of the floor design.
- **Admin acting on themselves**: a config/floor admin demoting/disabling
  themselves is refused by the floor rule (their email is in the set). A
  DB-promoted admin *may* demote themselves; that is allowed (the floor still
  exists), and is the same as any other DB-admin demotion.
- **Disabling an already-disabled user / enabling an active user**: idempotent
  and a **no-op** — if the user is already in the target state, make no change
  and write **no** audit row. Test this explicitly.
- **Handle-reset with no handle (resolves review findings G-3 / C-1)**: if the
  target has no handle, handle-reset is a **no-op** and writes **no** audit row.
  When it does reset, the field update and the audit write happen in the **same
  DB transaction** so they cannot diverge.
- **Disabled admin**: a disabled DB-promoted admin is blocked at auth before any
  admin check runs (disable enforcement precedes role checks).
- **Search with no matches / empty platform**: list renders an empty state.
- **Pagination boundary**: large user counts must not load every row at once.
  Search uses a case-insensitive substring match on `email` + `handle`; at the
  current platform scale a sequential scan is acceptable. A trigram/full-text
  index is a documented future option if the user table grows large enough to
  threaten SC-001 (resolves review findings G-2 / C-2).

## Requirements

### Functional Requirements

- **FR-001**: System MUST provide `GET /admin/users` (platform-admin only)
  rendering a **paginated** list (fixed page size, default 50 per page) of all
  users, with an optional `q` query that filters by **case-insensitive substring
  match on `email` and `handle`**. Supports US1.
- **FR-002**: The list MUST show, per user: email, handle, created date, agent
  count, admin status, and disabled status. Supports US1.
- **FR-003**: System MUST provide `GET /admin/users/{user_id}` (platform-admin
  only) showing the user's connections, agents, recent matches, role, disabled
  state, and audit history. "Recent matches" MUST be capped (e.g. most recent 20)
  and audit history MUST be bounded/paginated, so the page cannot fan out
  unbounded queries for a heavy user. Supports US2.
- **FR-004**: System MUST add a nullable `disabled_at` timestamp to `users`
  (NULL = active). Supports US3.
- **FR-005**: System MUST provide endpoints to disable and enable a user
  (platform-admin only), setting/clearing `disabled_at`. Supports US3.
- **FR-006**: A disabled user MUST be rejected on any authenticated **web**
  request and MUST NOT be able to establish a new session via Google login. The
  check lives in the web auth dependency (`require_user` in `app/deps.py`), not in
  the pure getter `get_user_from_session` (which stays `-> User | None`). Web
  routes redirect to a disabled-account notice page (303 → `/disabled`). The
  session is DB-backed — `app/auth/session.py` stores only `user_id` and
  re-fetches the `User` row every request — so the check takes effect on the very
  next request (no stateless-token gap). Supports US3, decision #3.
- **FR-006a (connection/runner auth path — resolves review findings C-HIGH /
  G-1 / G-2)**: A disabled user's **bot runners** MUST also be rejected. Runners
  authenticate via `X-Connection-Key` through `require_connection` (`app/deps.py`),
  a path entirely separate from the web session. `require_connection` already
  loads the `Connection` (which has `user_id`); it MUST additionally reject when
  the owning user is disabled, returning a structured JSON 403 with code
  `ACCOUNT_DISABLED` (same shape as the existing `CONNECTION_PAUSED` /
  `CONNECTION_DELETED` errors). This is what actually stops a disabled user from
  acting; without it, disable is cosmetic. Enforcement is still **auth-layer
  only** — we reject the runner's calls; we do not kill runner processes, cancel
  queued matches, or pull agents from active matches (those stay non-goals).
  Supports US3, decision #3.
- **FR-006b**: Disabled-auth responses MUST be content-appropriate: web deps
  (`require_user` and callers) return a 303 redirect to the `/disabled` notice;
  API/connection deps (`require_connection`) return a structured JSON 403
  (`ACCOUNT_DISABLED`). A simple public `/disabled` notice route MUST exist.
  Supports US3.
- **FR-007**: Disable/enable MUST NOT delete or modify the user's connections,
  agents, or match history. Supports US3, decision #1.
- **FR-008**: System MUST provide endpoints to promote a user to `ADMIN` and
  demote a user to `USER` (platform-admin only) by setting `User.role`.
  Supports US4, decision #2.
- **FR-009**: Login-sync (`sync_google_user`) MUST be additive: ensure `ADMIN`
  for emails in `platform_admin_emails_set`; otherwise preserve the stored role.
  It MUST NOT reset a DB-promoted admin to `USER`. Supports US4 (critical).
- **FR-010**: The system MUST refuse to demote or disable any user whose email is
  in `platform_admin_emails_set` (the immutable config floor), returning a clear
  error and making no change. The comparison MUST be case-insensitive —
  `target.email.lower()` against the set (which `config.py` already lowercases) —
  so a casing mismatch between the OAuth-stored email and the config value cannot
  silently bypass the floor (resolves review finding G-3). Supports US4.
- **FR-011**: System MUST record an audit row for every disable, enable, promote,
  demote, and handle-reset action that **changes state**, capturing
  `actor_user_id`, `target_user_id`, `action`, optional `reason` (free text,
  ≤ 500 chars), and `created_at`. No-op actions (already in target state; handle
  reset with no handle) write **no** row. The state change and the audit write
  MUST occur in the **same DB transaction**. Supports US5.
- **FR-012**: The user detail page MUST display audit rows for that target,
  newest-first. Supports US5.
- **FR-013**: All user-management endpoints MUST be gated by
  `require_platform_admin`; non-admin access MUST return 403. Supports all.
- **FR-014**: A new Alembic migration MUST add the `disabled_at` column and the
  audit-log table, chained off the current head (`0028_user_roles`), and MUST
  apply cleanly on the SQLite test DB using batch mode for any constraint ops.
  `AdminAuditLog` foreign keys to `users.id` use **`ON DELETE RESTRICT`**
  (intentional: the audit trail must survive; users are disabled, never
  hard-deleted — resolves review finding G-8).
- **FR-015**: A "Users" entry point MUST be added to the platform-admin
  navigation/dashboard, visible only to admins.
- **FR-016**: The existing `/admin/handles` management view MUST surface each
  user's disabled and admin status (badges), and the existing `admin_reset_handle`
  route MUST be retrofitted to write through the same audit path as FR-011 — so
  an already-shipped admin mutation is not left unaudited (resolves review
  findings G-4 / C-1). Supports US5.
- **FR-017**: The app MUST log a loud startup warning when
  `platform_admin_emails_set` is empty (advisory only; does not block boot), since
  an empty bootstrap list removes the immutable admin floor. The check MUST be
  evaluated **explicitly in the app startup/lifespan path** in `app/main.py`, not
  left to lazily fire from the config property or login sync (resolves review
  findings C-residual / C-MEDIUM).
- **FR-018**: A state-changing mutation (disable/enable/promote/demote) MUST lock
  the target user row (e.g. `SELECT ... FOR UPDATE`, or the SQLite-safe
  equivalent) within its transaction, so two concurrent admin actions cannot both
  pass the no-op check and write duplicate audit rows, honoring SC-004 (resolves
  review residual: audit race). Supports US5.

### Key Entities

- **User (existing, modified)**: add `disabled_at: datetime | None`. Reuse
  existing `role: UserRole`. No other changes.
- **AdminAuditLog (new)**: `id`, `actor_user_id` (FK users), `target_user_id`
  (FK users), `action` (enum/string: `disable`, `enable`, `promote`, `demote`,
  `handle_reset`), `reason: str | None`, `created_at`. Append-only.

## Success Criteria

- **SC-001**: A platform admin can find any specific user from `/admin/users` in
  under 10 seconds via search.
- **SC-002**: A disabled user is denied access on their very next request — no
  stale-session window where they can still act.
- **SC-003**: An in-app promotion persists across the promoted user logging out
  and back in (0% reset rate).
- **SC-004**: 100% of disable/enable/promote/demote/handle-reset actions produce
  exactly one corresponding audit row.
- **SC-005**: A non-admin receives 403 on every user-management endpoint, verified
  by direct-request tests (not just hidden UI).
- **SC-006**: `ruff`, `mypy app/ mcp_server/`, and `pytest -q` all pass; the new
  migration applies on the in-memory SQLite test DB.

## Assumptions

- Disable scope is **auth-block only** (decision Q1, corrected by spec review):
  disabled users are rejected at **both** auth paths — the web session
  (`require_user` → 303 `/disabled`) **and** the bot/connection key path
  (`require_connection` → 403 `ACCOUNT_DISABLED`). The original framing ("agents
  go inert because the owner can't act") was wrong: bot runners authenticate with
  their own connection keys, independent of the owner's web session, so the
  connection-path check (FR-006a) is required for disable to actually stop them.
  We still do **not** kill runner processes, cancel queued matches, pull agents
  from active matches, or hide them from leaderboards — disabled runners simply
  get 403s and stop making progress.
- Admin guardrail is **config admins are the immutable floor** (decision Q2):
  emails in `PLATFORM_ADMIN_EMAILS` cannot be demoted/disabled in-app; DB-promoted
  admins are fully mutable. No separate last-admin or self-demote logic is added,
  because a non-empty config floor already prevents zero-admin lockout.
- Audit logging is a **persistent table scoped to admin user-management actions**
  (decision Q3), not platform-wide auditing.
- The bootstrap email list (`PLATFORM_ADMIN_EMAILS`) is non-empty in any real
  deployment.
- Handle-reset already exists as an admin action; this feature routes it through
  the audit log but does not change its behavior.

## Non-Goals

- Hard-deleting users or cascade-deleting their connections/agents/match history.
- Halting running connection runners or pulling agents from active/queued matches
  on disable. **Documented limitation (review finding G-1):** disabling a user
  who has already queued many matches does not clear that queue, and there is no
  bulk match-cancel today (only one-by-one `admin_delete_match`). A bulk
  match-cancel tool is a recommended **follow-up feature**, tracked separately.
- Hiding a disabled user's agents from public leaderboards/standings.
- Platform-wide audit logging of non-admin actions (game moves, API polls, logins).
- Removing `PLATFORM_ADMIN_EMAILS` — it stays as the immutable bootstrap floor.
- Any change to game-admin scope or `game_admin_*` routes.
- Changes to match logic, the turn loop, scheduling, or bot seeding.
