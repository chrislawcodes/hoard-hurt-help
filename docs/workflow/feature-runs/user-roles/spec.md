# Spec: Admin and Regular User Roles

## Summary

Give the platform two user roles: **admin** and **user** (regular). Regular
users can create matches from a user-facing flow, cancel their own matches, and
delete their own matches before they start. Admins can delete or cancel any
match in any state. Matches gain an owner (`created_by_user_id`). The admin
role lives on the `users` table and is seeded from the existing
`PLATFORM_ADMIN_EMAILS` allowlist at login. A per-user active-match cap bounds
LLM spend from open match creation.

## Background — current state

- All signed-in users are equal. `app/models/user.py` has no role column.
- Admin access is email-based at request time:
  `require_platform_admin` (`app/deps.py:71`) checks
  `settings.platform_admin_emails_set`; `require_game_admin`
  (`app/deps.py:88`) checks `GAME_ADMIN_EMAILS__<GAME>` env vars
  (`app/config.py:98-126`).
- Match creation exists only in the game-admin form
  (`app/routes/game_admin_web.py:100-159`, `create_match_submit`) and the admin
  API. Matches have no creator column (`app/models/match.py`).
- Match deletion exists only for platform admins
  (`app/routes/admin_web.py:65-95`, `admin_delete_match`) — a careful cascade
  that stops the scheduler task, then deletes turn submissions, turn messages,
  turns, players, incidents, and the match.
- Match cancel exists only for game admins
  (`app/routes/game_admin_web.py:424-438`).
- Login creates/updates users in `sync_google_user` (`app/routes/auth.py:19`).

## Decisions (from discovery)

1. **Owner delete is pre-start only.** A match contains other users' players,
   turns, and scores. Owners may delete their own match only while it is in a
   pre-start state (`SCHEDULED` or `REGISTERING`). Admins may delete any match
   in any state.
2. **New user-facing create flow.** Regular users get a slim create-match
   action on the game pages. The game-admin form stays admin-only and
   unchanged.
3. **Role column, env-seeded.** `users.role` (`admin` | `user`) is the source
   of truth for platform-admin checks. It is synced from
   `PLATFORM_ADMIN_EMAILS` on every login (promote *and* demote), so the env
   var remains the bootstrap mechanism — no admin-management UI in this
   feature.
4. **Owner cancel is allowed anytime the match is cancellable** (same state
   rule as the existing game-admin cancel: not `COMPLETED`/`CANCELLED`).
   Cancel preserves data, so it is safe for owners.
5. **Active-match cap.** A regular user may have at most N matches they
   created in a non-terminal state (`SCHEDULED`, `REGISTERING`, `ACTIVE`).
   Default 3, configurable via `USER_ACTIVE_MATCH_LIMIT`. Admins are exempt.

## Functional requirements

### FR-1: Role column on users

- Add `role` to `User` (`app/models/user.py`): non-null string-backed enum
  (`UserRole.ADMIN` / `UserRole.USER`), `server_default='user'`, following the
  `FlexibleEnumType` pattern used by `Match.state`.
- Alembic migration `0028_*` adds the column with a server default so existing
  rows backfill to `user`. Must run on SQLite (tests) and Postgres (prod).
- `sync_google_user` (`app/routes/auth.py`) sets
  `role = ADMIN if email in settings.platform_admin_emails_set else USER` on
  every login. Removing an email from the allowlist demotes that user at their
  next login.

### FR-2: Role-based admin check

- `require_platform_admin` (`app/deps.py`) checks `user.role == UserRole.ADMIN`
  instead of the email set. Error shape and code (`NOT_PLATFORM_ADMIN`)
  unchanged.
- `require_game_admin` is untouched (non-goal).

### FR-3: Match ownership

- Add `created_by_user_id` to `Match` (`app/models/match.py`): nullable FK to
  `users.id`, indexed. Existing matches stay `NULL` (admin-managed only —
  no owner, so only admins can delete/cancel them).
- Same `0028_*` migration (or a sibling) adds the column. SQLite needs
  `batch_alter_table` for the FK.
- All creation paths record the creator: the new user-facing flow, the
  game-admin form (`game_admin_web.py`), and the admin API. System-created
  matches (practice arena, auto-scheduled in `app/engine/arena.py` /
  scheduler) stay `NULL`.

### FR-4: User-facing match creation

- New route module `app/routes/matches_user.py` (no `utils.py`-style names):
  - `GET /games/{game}/matches/new` — slim create form, `require_user`.
  - `POST /games/{game}/matches/new` — `require_user`, creates the match with
    `created_by_user_id = user.id`.
- The slim form exposes **name** and **scheduled start** only; player counts,
  rounds, and deadlines use the existing defaults. Validation rules match the
  admin form (future start time, known game).
- Extract the shared creation logic (ID allocation + row construction +
  validation) out of `create_match_submit` (`game_admin_web.py:100-159`) into
  a shared module (e.g. `app/engine/match_creation.py`) so the admin form and
  the user flow do not duplicate it.
- Enforce the cap here: count matches with
  `created_by_user_id == user.id AND state IN (SCHEDULED, REGISTERING, ACTIVE)`;
  reject creation at the limit with a clear error. Admins are exempt.
- Lobby (`app/routes/web_lobby.py` + template) gets a "Create match" action
  for signed-in users.

### FR-5: Owner delete (pre-start) and admin delete

- Extract the delete cascade from `admin_delete_match`
  (`app/routes/admin_web.py:65-95`) into a shared module (e.g.
  `app/engine/match_deletion.py`) — single implementation of the
  scheduler-stop + ordered-delete sequence.
- New route `POST /matches/{match_id}/delete` (in `matches_user.py`):
  allowed when `user.role == ADMIN`, or when
  `match.created_by_user_id == user.id AND match.state IN (SCHEDULED, REGISTERING)`.
  Otherwise 403 (`NOT_MATCH_OWNER`) / 409 for owned-but-started
  (`MATCH_ALREADY_STARTED`).
- The existing `/admin/matches/{match_id}/delete` route stays and now calls
  the shared cascade.
- Owner-visible delete control on the lobby/match pages for matches the
  signed-in user owns and that are pre-start; admins see it on everything
  (admin dashboard already has it).

### FR-6: Owner cancel and admin cancel

- New route `POST /matches/{match_id}/cancel` (in `matches_user.py`):
  allowed for admins on any match, and for owners on their own match; rejected
  with 409 when the match is already `COMPLETED`/`CANCELLED` (same rule as the
  game-admin cancel at `game_admin_web.py:424-438`). Sets
  `state = CANCELLED`, `cancelled_at = now`, and stops the scheduler task.
- Reuse/extract the cancel logic shared with the game-admin route rather than
  duplicating it.

### FR-7: Configuration

- New setting `user_active_match_limit: int = 3` in `app/config.py`
  (env `USER_ACTIVE_MATCH_LIMIT`).

## Acceptance criteria

1. A signed-in regular user can create a match from the user-facing flow; the
   match records them as creator.
2. A regular user can delete their own match only while it is pre-start
   (`SCHEDULED`/`REGISTERING`); delete of a started match they own returns 409;
   delete/cancel of a match they don't own returns 403.
3. An admin can delete or cancel any match in any state (cancel still 409s on
   already-terminal states).
4. A regular user at the active-match cap gets a clear rejection when creating
   another; admins are exempt from the cap.
5. Users whose email is in `PLATFORM_ADMIN_EMAILS` have `role=admin` after
   login; removing the email demotes at next login; everyone else is
   `role=user`.
6. Signed-out users cannot create, delete, or cancel matches (401).
7. Existing admin routes behave as before (same URLs, same error codes).

## Test plan

- New tests in `tests/` (SQLite in-memory, per repo testing rules):
  role seeding/demotion at login (extend `tests/test_auth_user_sync.py`),
  user create flow + cap, owner delete state matrix (pre-start / active /
  not-owner / admin), owner cancel matrix, ownership recorded on each creation
  path.
- Existing admin tests (`tests/test_admin.py`, `tests/test_config_admin.py`)
  must keep passing — they pin the admin error codes and flows.

## Non-goals

- No permissions tables, role hierarchies, or admin-management UI.
- No change to viewing: matches and leaderboards stay publicly readable.
- No change to the per-game admin (`GAME_ADMIN_EMAILS__*`) mechanism or the
  admin web forms beyond recording the creator.
- No soft-delete/archive system; no tournaments or leagues.
- No changes to agent/connection ownership rules.

## Risks

- **Role demotion timing**: a demoted admin keeps `role=admin` until their
  next login. Accepted: sessions are the same trust boundary the email check
  had (email checks also only ran per-request on a live session; the allowlist
  is operator-controlled either way).
- **Migration on prod Postgres**: adding a non-null column with a server
  default and a nullable FK is metadata-only/cheap at this table size.
- **Delete-cascade extraction**: moving the cascade must preserve the
  scheduler-stop-first ordering and the second submission-sweep pass
  (`admin_web.py:74-87`) — covered by reusing the code verbatim, not
  rewriting it, plus the existing admin delete tests.
- **Cap races**: two concurrent creates could both pass the count check.
  Accepted for this feature — worst case is limit+1 matches, no data harm.

## Scope paths

- `app/` (models, routes, deps, config, engine extraction, templates)
- `migrations/` (one new revision)
