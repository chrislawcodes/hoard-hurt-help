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
- **One-time backfill so existing admins are not locked out.** Because
  `require_platform_admin` switches to reading `user.role` immediately (see FR-2)
  but `sync_google_user` only writes `role` at login, the migration must also
  promote already-created admin rows: in the same `0028_*` revision, after adding
  the column with `server_default='user'`, run an `UPDATE users SET role='admin'
  WHERE lower(email) IN (<PLATFORM_ADMIN_EMAILS>)` sourced from
  `settings.platform_admin_emails_set` at upgrade time. Without this, every
  current platform admin (prod) is demoted until their next Google login.
  *Plan-stage verification:* confirm `PLATFORM_ADMIN_EMAILS` is present in the
  Railway deploy env when the migration runs, and assert the promoted row count
  matches the allowlist size (see Risks).
- **Refresh the stored email at login.** `sync_google_user` keys users on
  `google_sub` and currently never updates `User.email` for an existing row, so
  role seeding (which matches on email) would trust the email captured at first
  signup. Update `sync_google_user` to refresh `User.email` from the current
  Google identity before computing `role`, so a changed Google email can't leave
  an admin mis-seeded or a demotion stuck.

### FR-2: Role-based admin check

- `require_platform_admin` (`app/deps.py`) checks `user.role == UserRole.ADMIN`
  instead of the email set. Error shape and code (`NOT_PLATFORM_ADMIN`)
  unchanged.
- The template-chrome helper `_is_any_admin` (`app/routes/web_support.py:47`)
  derives the *platform-admin* half of its check from `user.role == ADMIN`
  instead of `settings.platform_admin_emails_set`, so admin UI chrome stays
  consistent with the route guards. The game-admin half (the
  `all_game_admin_emails_set` check) stays email-based — `require_game_admin`
  and `GAME_ADMIN_EMAILS__*` are untouched (non-goal).
- `require_game_admin` is untouched (non-goal).

### FR-3: Match ownership

- Add `created_by_user_id` to `Match` (`app/models/match.py`): nullable FK to
  `users.id`, indexed. Existing matches stay `NULL` (admin-managed only —
  no owner, so only admins can delete/cancel them).
- Same `0028_*` migration (or a sibling) adds the column. SQLite needs
  `batch_alter_table` for the FK.
- **Every human creation path records the creator** (the acting user's id) and
  routes through the shared `match_creation.py` helper — there are four, not
  one "admin API":
  1. new user-facing flow (`matches_user.py`, FR-4) — creator = the user;
  2. game-admin web form (`game_admin_web.py` `create_match_submit:100-159`);
  3. game-admin JSON API (`game_admin_api.py` `POST /matches:33-78`);
  4. platform-admin JSON API (`admin_api.py` `POST /matches` + `/games:25-58`).
  For the three admin paths the creator is the acting admin user; the cap is not
  enforced (admins exempt). System-created matches (practice arena,
  auto-scheduled in `app/engine/arena.py` / scheduler) stay `NULL`. If any of
  the four human paths is left building `Match(...)` inline, it silently creates
  ownerless matches and bypasses owner/cap semantics — so the reuse audit must
  confirm all four adopt the shared helper.

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
- **ID-collision handling.** The current ID allocator is non-atomic
  (`create_match_submit` and `arena._next_match_id` both scan all `Match.id`
  values and compute `max + 1` in app code). Two concurrent creates — more
  likely now that the flow is user-facing — can pick the same `M_####` and hit
  a primary-key conflict. The shared `match_creation.py` must catch
  `IntegrityError` on insert and retry ID allocation a bounded number of times
  (e.g. 3) before surfacing an error. This also hardens the existing admin path.
- **Single allocation path.** To actually close the collision (not just the
  user/admin half), `arena._next_match_id` (`app/engine/arena.py`) — the other
  non-atomic `max + 1` allocator — must route through the same shared
  allocator+retry so arena/auto-match creates and user/admin creates can't pick
  the same `M_####`. The Design-stage reuse audit should flag this duplicate
  allocator and the plan should converge both paths on `match_creation.py`.
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
- **NULL-owner (system/arena) matches** have no owner, so a non-admin's
  ownership check (`created_by_user_id == user.id`) never matches — only admins
  can delete or cancel them via the new routes. Arena auto-management
  (`ensure_practice_arena` creating/cancelling its own matches in
  `app/engine/arena.py`) is unchanged and unaffected by these owner routes.
- Owner-visible delete control on the lobby/match pages for matches the
  signed-in user owns and that are pre-start; admins see it on everything
  (admin dashboard already has it).
- **Owner read model.** A creator who has not joined as a player has no page
  that shows their match today: `/me/matches` (`web_player.py:397-404`) is built
  only from `Player.user_id`, so a freshly created, empty future match is
  invisible to its owner. Extend `/me/matches` to also include matches where
  `created_by_user_id == user.id` (union with the player-based set, de-duped),
  and render the owner delete/cancel controls on those rows. This is where the
  owner finds and manages a match nobody has joined yet.

### FR-6: Owner cancel and admin cancel

- New route `POST /matches/{match_id}/cancel` (in `matches_user.py`):
  allowed for admins on any match, and for owners on their own match. Cancel is
  allowed from **any non-terminal state** (`SCHEDULED`, `REGISTERING`, *and*
  `ACTIVE`) and rejected with 409 only when the match is already
  `COMPLETED`/`CANCELLED`. Sets `state = CANCELLED`, `cancelled_at = now`, and
  stops the scheduler task.
- **Only the new routes allow cancelling `ACTIVE`; the existing game-admin
  cancel is unchanged.** Today `game_admin_cancel_match`
  (`game_admin_web.py:424-438`) rejects `ACTIVE` with 409 ("Match already
  started"). To honor Acceptance criterion 7 (existing admin routes keep the
  same error codes), that route keeps its current `ACTIVE` guard. Factor the
  state transition (stop scheduler → `state = CANCELLED`, `cancelled_at = now`)
  into the shared helper so both surfaces share one implementation, but the
  **caller** owns the allowed-state policy: the new owner/admin routes accept
  any non-terminal state (incl. `ACTIVE`), while the game-admin route keeps its
  pre-check that rejects `ACTIVE`. No behavior change to the existing route.
- **The admin dashboard's cancel control retargets to the new route so admins
  can actually cancel `ACTIVE` matches** (settled decision: admins cancel any
  state). Today the platform admin dashboard (`app/templates/admin/dashboard.html`)
  posts to `/api/admin/matches/{id}/cancel` (`admin_api.py:68`), which 409s on
  `ACTIVE`. Point that control at the new role-based `/matches/{id}/cancel`
  route instead. The legacy `/api/admin/...` and `/api/game-admin/.../cancel`
  routes are left unchanged (AC-7 — same URLs, same error codes); only the
  template's POST target changes, which is not a route-behavior change.

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
  not-owner / admin), owner cancel matrix, ownership recorded on **each of the
  four human creation paths** (user flow, game-admin web, game-admin API,
  platform-admin API), the owner read model (`/me/matches` shows a created-but-
  not-joined match with owner controls), and email refresh at login (changed
  Google email updates `User.email` and re-evaluates `role`).
- **Admin test fixtures must seed `role`.** `_seed_user` in `tests/test_admin.py`
  creates `User` rows with only `email`/`google_sub`; with the role-based guard
  they would default to `role=user` and the admin-dashboard tests would 403.
  Update the admin fixtures (or `_seed_user`) to set `role=ADMIN` for admin
  users so `tests/test_admin.py` and `tests/test_config_admin.py` keep pinning
  the admin error codes and flows.
- **Admin-chrome fixture sweep.** Switching the platform-admin half of
  `_is_any_admin` to `user.role` (FR-2) breaks any test that grants admin chrome
  by monkeypatching the email allowlists. Audit and update the tests that set
  `settings.admin_emails` / `platform_admin_emails` and assert admin UI —
  `tests/test_lobby.py`, `tests/test_bot_form_validation.py`,
  `tests/test_handle_safety.py`, `tests/test_request_logging.py` — to seed
  `role=ADMIN` on the user instead.
- **New-route cancel covers `ACTIVE`; game-admin cancel stays 409 on `ACTIVE`.**
  Assert the new owner/admin cancel route cancels an `ACTIVE` match, and that
  the existing game-admin cancel still returns 409 for `ACTIVE` (AC-7 — no
  behavior change to the existing route).
- **Shared delete-cascade regression.** A test that runs the extracted
  `match_deletion.py` against a match with in-flight turn submissions, asserting
  scheduler-stop-first ordering and full FK cleanup (covers the order-sensitive
  cascade).
- **Migration backfill test.** Assert the `0028_*` migration promotes a
  pre-seeded user whose email is in the allowlist to `role=admin` and leaves
  others at `user`.

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
  *verification:* the cap-test in the test plan asserts the count query and
  rejection path; the race itself is best-effort and left unguarded by design.
- **Match-ID collision under concurrency**: the `max + 1` allocator can produce
  a duplicate `M_####` on simultaneous creates. Mitigated by the bounded
  `IntegrityError` retry in `match_creation.py` (FR-4). *verification:* a test
  that forces an insert-time `IntegrityError` once and asserts the create
  succeeds on retry with the next free ID.
- **Existing-admin backfill correctness**: the migration promotes current
  admins from `PLATFORM_ADMIN_EMAILS` (FR-1). If the env var is absent at
  migration time, all admins silently fall back to `role=user`. *verification:*
  the plan confirms `PLATFORM_ADMIN_EMAILS` is set in the Railway deploy env
  before the migration runs, and the post-deploy check asserts the promoted
  `role='admin'` row count equals the allowlist size.

## Scope paths

- `app/` (models, routes, deps, config, engine extraction, templates)
- `migrations/` (one new revision)
