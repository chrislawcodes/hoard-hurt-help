# Tasks: Admin and Regular User Roles

Executable slices from `plan.md`. Each `[CHECKPOINT]` is a diff boundary
(≤ ~300 changed lines). Implement top to bottom; run the Preflight Gate
(`ruff check . && mypy app/ mcp_server/ && pytest -q`) at every checkpoint.

Dependency order: S1 (schema) → {S2 (auth), S3a (create), S3b (teardown)} → S4
(routes). S2 / S3a / S3b touch disjoint files and could run in parallel after
S1, but each is implemented as its own checkpoint slice (see Parallel analysis).

---

## Slice 1 — Schema foundation + config  (~120 lines)

- [ ] `app/models/user.py`: add `class UserRole(str, enum.Enum)` (`ADMIN="admin"`,
      `USER="user"`) and a `role` column using the `Agent.kind` shape
      (`app/models/agent.py:46-51`): `FlexibleEnumType(UserRole)`,
      `nullable=False`, `default=UserRole.USER`, `server_default="user"`.
- [ ] `app/models/match.py`: add `created_by_user_id` — **nullable**, indexed FK
      → `users.id` (mirror the `winner_player_id` FK pattern at `match.py:69-72`,
      `use_alter=True`). Existing rows stay NULL; **no NOT-NULL, no default
      backfill needed** (rejects Gemini plan-residual-1).
- [ ] `app/config.py`: add `user_active_match_limit: int = Field(default=3)`
      (env `USER_ACTIVE_MATCH_LIMIT` binds automatically).
- [ ] `migrations/versions/0028_user_roles.py`:
      - add `users.role` (`server_default="user"`) and `matches.created_by_user_id`
        (nullable FK), using `op.batch_alter_table` for the SQLite FK add;
      - **idempotent backfill** in the same revision (one transaction): `UPDATE
        users SET role='admin' WHERE lower(email) IN (:allowlist)` where
        `:allowlist` = `settings.platform_admin_emails_set` (already lowercased;
        includes the legacy `ADMIN_EMAILS` fallback — intentional, mirrors the
        prior check). A stale/typo allowlist email simply matches no row (no-op);
        that admin is promoted at next login via `sync_google_user`.
      - `downgrade()` drops both columns.
- [ ] Tests: `tests/test_migrations.py` round-trip stays green; new case asserts
      the backfill promotes a pre-seeded allowlisted user to `role='admin'` and
      leaves a non-allowlisted user at `'user'`; model default is `'user'`.

**Verify:** `alembic upgrade head` + `downgrade` round-trips on SQLite; backfill
test passes.

- [x] Slice 1 checkpoint — Preflight Gate (ruff + mypy + pytest) green, then diff checkpoint. [CHECKPOINT]

---

## Slice 2 — Role seeding + role-based admin check  (~110 lines)

- [ ] `app/routes/auth.py` `sync_google_user`:
      - refresh `user.email` from `userinfo.email` for existing rows, **guarded
        against the `users.email` unique constraint** — if another row already
        holds that email, skip the update and log a warning (do not 500;
        `google_sub` is the real identity key);
      - set `user.role = UserRole.ADMIN if user.email.lower() in
        settings.platform_admin_emails_set else UserRole.USER` on **every** login
        (promote *and* demote); the **new-user branch also sets `role`**.
      - Note the explicit `.lower()` — the allowlist set is already lowercased
        (matches the `deps.py` / `web_support.py` convention).
- [ ] `app/deps.py` `require_platform_admin`: replace the email check with
      `user.role != UserRole.ADMIN` (keep the `NOT_PLATFORM_ADMIN` code + error
      shape). Do **not** touch `require_game_admin`.
- [ ] `app/routes/web_support.py` `_is_any_admin`: platform half →
      `user.role == UserRole.ADMIN`; keep the game-admin half email-based.
- [ ] Tests: extend `tests/test_auth_user_sync.py` — allowlisted email →
      `role=admin`; removing the email demotes at next login; non-allowlisted →
      `user`; email-collision skip path logs and does not raise.
- [ ] **Admin-chrome fixture sweep** — set `role=UserRole.ADMIN` on seeded admin
      users in: `tests/test_admin.py` (`_seed_user`), `tests/test_config_admin.py`,
      `tests/test_lobby.py`, `tests/test_bot_form_validation.py`,
      `tests/test_handle_safety.py`, `tests/test_request_logging.py` — wherever
      they assert admin chrome / dashboard access.

**Verify:** full `pytest -q` green.

- [x] Slice 2 checkpoint — Preflight Gate (ruff + mypy + pytest) green, then diff checkpoint. [CHECKPOINT]

---

## Slice 3a — Shared creation helper + caller convergence  (~180 lines)

- [ ] New `app/engine/match_creation.py`: one async `create_match(...)` that does
      id-allocation (wraps `tokens.generate_match_id`; the `max+1` scan lives here
      only) → validation (future start time, known game, player/round/deadline
      defaults min 3 / max 100 / deadline 60) → `Match(...)` build with
      `created_by_user_id` → insert with **bounded `IntegrityError` retry** (≤3:
      on PK collision re-allocate the id and retry). Param `enforce_cap: bool`
      (and the acting user) so admin paths skip the cap; the cap counts matches
      with `created_by_user_id == user.id AND state IN (SCHEDULED, REGISTERING,
      ACTIVE)` and rejects at the limit with a clear error.
- [ ] Converge **all five** allocators / four builders on the helper:
      `game_admin_web.create_match_submit`, `admin_api.create_game`,
      `game_admin_api.create_game` (record the acting admin as creator, cap off),
      and `arena._next_match_id` / arena creation (pass `created_by=None`, cap
      off). No inline `max+1` or `Match(...)` may remain in these paths.
- [ ] Tests: each human path records `created_by_user_id`; arena-created matches
      stay NULL; monkeypatch the allocator to collide once → create still
      succeeds with the next free id (ID-collision retry); cap rejects at limit,
      admin exempt.

**Verify:** `pytest -q`; grep confirms no residual inline `max+1` in the four
human creators.

- [x] Slice 3a checkpoint — Preflight Gate (ruff + mypy + pytest) green, then diff checkpoint. [CHECKPOINT]

---

## Slice 3b — Shared deletion/cancel helper + caller convergence  (~160 lines)

- [ ] New `app/engine/match_deletion.py`:
      - `delete_match(...)` — move the cascade **verbatim** from
        `admin_web.admin_delete_match:65-95` (scheduler-stop-first → submissions →
        messages → turns → second submission/message sweep by player → null
        `winner_player_id` → players → incidents → match). Do not rewrite.
      - `cancel_match(...)` — the shared transition only: `registry.stop(id)` →
        `state=CANCELLED` → `cancelled_at=now` → commit.
- [ ] Refactor callers to delegate, **keeping their own allowed-state policy**
      (AC-7): `admin_web.admin_delete_match` → `delete_match`; the three cancel
      sites (`game_admin_web.game_admin_cancel_match`, `admin_api.cancel_game`,
      `game_admin_api.cancel_game`) → `cancel_match`, each keeping its existing
      `ACTIVE`/terminal pre-checks and error codes unchanged.
- [ ] Tests: delete-cascade regression with an in-flight turn submission asserts
      scheduler-stop-first ordering + `winner_player_id` nulled + full FK cleanup;
      existing admin delete + game-admin/admin cancel tests stay green
      (`ACTIVE → 409` preserved on the legacy routes).

**Verify:** `pytest -q`; the cascade regression reproduces the two-pass-sweep
scenario.

- [x] Slice 3b checkpoint — Preflight Gate (ruff + mypy + pytest) green, then diff checkpoint. [CHECKPOINT]

---

## Slice 4 — User-facing routes + read model + templates  (~250 lines)

- [ ] New `app/routes/matches_user.py` (`require_user`):
      - `GET /games/{game}/matches/new` — slim form (name + scheduled start only).
      - `POST /games/{game}/matches/new` — create via `match_creation.create_match`
        with `created_by_user_id=user.id`, `enforce_cap=True`; cap rejection shows
        a clear message.
      - `POST /matches/{id}/delete` — allowed when `user.role==ADMIN`, or
        `match.created_by_user_id==user.id AND state IN (SCHEDULED, REGISTERING)`;
        else 403 `NOT_MATCH_OWNER` / 409 `MATCH_ALREADY_STARTED`. Delegates to
        `match_deletion.delete_match`.
      - `POST /matches/{id}/cancel` — admin any match / owner own match, any
        non-terminal state (incl. `ACTIVE`); 409 if already COMPLETED/CANCELLED.
        Delegates to `match_deletion.cancel_match`.
      - Register the router in `app/routes/web.py` + `app/main.py`.
- [ ] `app/routes/web_player.py` `my_matches`: union matches where
      `created_by_user_id == user.id` with the player-based set, de-dupe by match
      id, render owner delete/cancel controls (delete only when pre-start).
- [ ] Templates: lobby "Create match" action for signed-in users (`home.html` via
      `web_lobby`); admin dashboard cancel control **retargets** its POST to the
      new `/matches/{id}/cancel` (`app/templates/admin/dashboard.html`) so admins
      can cancel `ACTIVE`; owner controls on `/me/matches` + match pages.
- [ ] Tests: user create + cap rejection; owner delete matrix (pre-start ok /
      active→409 / not-owner→403 / admin→any); owner cancel matrix; `/me/matches`
      shows a created-but-unjoined match with owner controls; signed-out →401 on
      create/delete/cancel; admin cancels an `ACTIVE` match via the new route;
      game-admin cancel still →409 on `ACTIVE` (AC-7).

**Verify:** `pytest -q`; preview the create flow + owner controls if previewable.
If this slice exceeds ~300 lines, split 4a (routes + read model) from 4b
(templates) at the router-registered boundary.

- [x] Slice 4 checkpoint — Preflight Gate (ruff + mypy + pytest) green, then diff checkpoint. [CHECKPOINT]

---

## Parallel analysis

After Slice 1 lands, Slices 2, 3a, and 3b edit disjoint file sets
(auth/deps/web_support vs match_creation+creators vs match_deletion+cancel-sites)
and have no shared writes, so they are conflict-free to parallelize. Slice 4
depends on all of 3a/3b (engine helpers) and 2 (role guard). The main rollout
implements each as its own checkpoint slice for clean diff review; parallel
dispatch is optional and not required for correctness.

## Post-merge verification (data-critical: migration 0028)

- Confirm `PLATFORM_ADMIN_EMAILS` is set in the Railway deploy env before the
  migration runs.
- After deploy: assert `SELECT count(*) FROM users WHERE role='admin'` equals the
  allowlist size (account for the legacy `ADMIN_EMAILS` fallback) before
  declaring the feature live; spot-check one known admin still has dashboard
  access and one removed admin is demoted after re-login.
