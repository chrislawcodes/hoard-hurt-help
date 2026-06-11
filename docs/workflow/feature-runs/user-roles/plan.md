# Plan: Admin and Regular User Roles

Route to build the design settled in `spec.md` + `reuse-report.md`. Every reuse
verdict is honored below; the build is sliced into checkpoint-bounded units, each
≤ ~300 changed lines, at stable interface boundaries.

## Architecture decisions

1. **`users.role` is the platform-admin source of truth.** New `UserRole`
   (`admin`|`user`) string enum on `User`, built with `FlexibleEnumType` +
   `nullable=False` + `server_default='user'` — copy the `Agent.kind` shape
   (`app/models/agent.py:46-51`), the live exemplar (reuse verdict). Seeded from
   `PLATFORM_ADMIN_EMAILS` at login; read by `require_platform_admin` and the
   `_is_any_admin` chrome helper.
2. **`matches.created_by_user_id`** — nullable, indexed FK → `users.id`, using
   the existing `match.py` FK pattern (`winner_player_id`, `use_alter=True`).
   Human-created matches record the creator; system/arena matches stay `NULL`.
3. **One shared creation path — `app/engine/match_creation.py` (justified-new).**
   Consolidates the four inline `Match(...)` builders *and* the five non-atomic
   `max+1` allocators (`game_admin_web.py:142`, `admin_api.py:36`,
   `game_admin_api.py:47`, `arena._next_match_id` used twice). One function does:
   id-allocation (wrapping the existing `tokens.generate_match_id` formatter,
   reuse) → validation → `Match(...)` build with `created_by_user_id` → insert
   with bounded `IntegrityError` retry (re-allocate id on PK collision). An
   `enforce_cap`/owner parameter lets admin paths skip the cap.
4. **One shared teardown path — `app/engine/match_deletion.py` (justified-new).**
   The order-sensitive delete cascade moves **verbatim** from
   `admin_web.admin_delete_match:65-95` (scheduler-stop-first; two-pass
   submission sweep; null `winner_player_id` before deleting players). The shared
   **cancel transition** (`registry.stop` → `state=CANCELLED` → `cancelled_at`)
   is factored here too; **callers keep their own allowed-state policy** so the
   existing routes' error codes are unchanged (AC-7).
5. **One new thin route module — `app/routes/matches_user.py` (justified-new).**
   `require_user` auth + per-user cap + owner/admin policy + delegation to the
   two engine helpers. No business logic duplicated.
6. **Migration `0028`** adds both columns and backfills `role='admin'` for emails
   in `PLATFORM_ADMIN_EMAILS` at upgrade time (single revision = one
   transaction). SQLite needs `batch_alter_table` for the FK add.

## Reuse-report coverage (every row addressed)

- **reuse**: `FlexibleEnumType`/`Agent.kind` shape, `config.Settings` field,
  `tokens.generate_match_id`, `scheduler.registry.stop`, `deps.require_user`,
  `web_support._load_match_or_404` — used as-is.
- **extend**: `require_platform_admin` (email→role), `_is_any_admin` (platform
  half→role), `sync_google_user` (email refresh + role), `my_matches` (union
  owner matches), lobby surface (`home.html` create action), `match.py` (FK),
  the three cancel sites (factor transition, keep policy), the `max+1` allocator
  (wrap in shared allocator).
- **justified-new**: `match_creation.py`, `match_deletion.py`,
  `matches_user.py` — each a consolidation point, justified in the reuse report.

## Slices (checkpoint-bounded)

### Slice 1 — Schema foundation + config  `[CHECKPOINT]`  (~120 lines)
- `app/models/user.py`: `UserRole` enum + `role` column (Agent.kind shape).
- `app/models/match.py`: `created_by_user_id` nullable indexed FK.
- `app/config.py`: `user_active_match_limit: int = Field(default=3)`.
- `migrations/versions/0028_user_roles.py`: add both columns (`batch_alter_table`
  for the FK on SQLite); backfill `role='admin'` from
  `settings.platform_admin_emails_set`. `downgrade()` drops both.
- Tests: model default (`role='user'`); migration backfill promotes an allowlist
  email and leaves others `user` (`tests/test_migrations.py` + a new case).
- **Verification:** `alembic upgrade head` then `downgrade` round-trips on SQLite
  (existing `test_migrations.py` guard); backfill test asserts promoted vs not.
- **Boundary rationale:** schema + config land before any caller reads them.

### Slice 2 — Role seeding + role-based admin check  `[CHECKPOINT]`  (~110 lines)
- `app/routes/auth.py` `sync_google_user`: refresh `user.email` from
  `userinfo.email` (guarded against the `users.email` unique constraint:
  skip+log on collision — `google_sub` is the real key), then set role
  (promote/demote); the new-user branch also sets role.
- `app/deps.py` `require_platform_admin`: `user.role != UserRole.ADMIN`.
- `app/routes/web_support.py` `_is_any_admin`: platform half → `user.role`.
- Tests: role seed/demote at login (`tests/test_auth_user_sync.py`); **admin
  fixture sweep** — `tests/test_admin.py` `_seed_user` (+ `test_config_admin.py`,
  `test_lobby.py`, `test_bot_form_validation.py`, `test_handle_safety.py`,
  `test_request_logging.py`) set `role=ADMIN` where they assert admin chrome.
- **Verification:** full `pytest -q` green; a login with an allowlisted email
  yields `role=admin`, removal demotes at next login (test asserts both).
- **Boundary rationale:** the auth guard flips here; isolating it makes the
  fixture sweep a self-contained, reviewable diff.

### Slice 3a — Shared creation helper + caller convergence  `[CHECKPOINT]`  (~180 lines)
- New `app/engine/match_creation.py`: allocator (wraps `generate_match_id`) +
  `IntegrityError`-retry, validation, owner, optional cap.
- Refactor all four human creators + `arena._next_match_id` to call it
  (creators record `created_by_user_id`; arena passes `None`, no cap).
- Tests: each path records the creator; arena stays `NULL`; forced
  `IntegrityError` once → retry succeeds with the next id.
- **Verification:** `pytest -q`; a test that monkeypatches the allocator to
  collide once asserts the create still succeeds (ID-collision retry).
- **Boundary rationale:** stable function signature before teardown work.

### Slice 3b — Shared deletion/cancel helper + caller convergence  `[CHECKPOINT]`  (~160 lines)
- New `app/engine/match_deletion.py`: delete cascade (verbatim move) + cancel
  transition.
- Refactor `admin_web.admin_delete_match` and the three cancel sites
  (`game_admin_web`, `admin_api`, `game_admin_api`) to use them; callers keep
  their allowed-state pre-checks (AC-7).
- Tests: delete cascade regression with an in-flight turn submission (asserts
  scheduler-stop-first ordering + `winner_player_id` null + full FK cleanup);
  existing admin delete/cancel tests stay green.
- **Verification:** `pytest -q`; the cascade regression test reproduces the
  two-pass-sweep scenario and passes.
- **Boundary rationale:** teardown is independent of creation; small diff.

### Slice 4 — User-facing routes + read model + templates  `[CHECKPOINT]`  (~250 lines)
- New `app/routes/matches_user.py`: `GET/POST /games/{game}/matches/new`
  (cap-enforced create; defaults min 3 / max 100 / deadline 60), `POST
  /matches/{id}/delete` (owner pre-start | admin any), `POST /matches/{id}/cancel`
  (owner | admin, any non-terminal). Register the router (`app/routes/web.py` /
  `app/main.py`).
- `app/routes/web_player.py` `my_matches`: union owner matches
  (`created_by_user_id == user.id`), de-duped; render owner controls.
- Templates: lobby "Create match" action (`home.html`); admin dashboard cancel
  control retargets to the new route (`templates/admin/dashboard.html`); owner
  delete/cancel controls on `/me/matches` + match pages.
- Tests: user create + cap rejection; owner delete matrix (pre-start / active-409
  / not-owner-403 / admin-any); owner cancel matrix; `/me/matches` shows a
  created-but-unjoined match; signed-out 401; admin cancels `ACTIVE` via the new
  route; game-admin cancel still 409s on `ACTIVE`.
- **Verification:** `pytest -q`; manual preview of the create flow + owner
  controls if previewable.
- **Boundary rationale:** the UI surface sits on top of settled engine + auth;
  if it grows past ~300 lines, split routes (4a) from templates/read-model (4b)
  at the route-registered boundary.

## Residual Risks (each verifiable)

- **Migration backfill depends on deploy-env parity.** If `PLATFORM_ADMIN_EMAILS`
  is missing/stale at Alembic runtime, admins fall back to `role=user`.
  *verification:* confirm `PLATFORM_ADMIN_EMAILS` is set in the Railway deploy
  env before the migration runs; post-deploy, assert the `role='admin'` row count
  equals the allowlist size (note the legacy `ADMIN_EMAILS` fallback can widen
  the set) before declaring the feature live.
- **Email refresh can hit the `users.email` unique constraint.** Two rows can't
  both hold one Google email, but an orphaned/stale row could collide.
  *verification:* a test that pre-seeds a second row holding the target email and
  asserts login skips the email update + logs, without 500ing.
- **Match-ID collision under concurrency.** *verification:* the Slice 3a test
  that forces one `IntegrityError` and asserts the retry picks the next free id.
- **Delete-cascade ordering on extraction.** *verification:* the Slice 3b
  regression test with an in-flight submission asserting stop-first +
  `winner_player_id` null + full FK cleanup.
- **Active-match cap race (best-effort by design).** Overshoot is limit+1, no
  data harm. *verification:* the cap test asserts the count query and the
  rejection path; the race itself is intentionally unguarded.
- **`_next_match_id` full-scan perf at scale (deferred).** Correctness is covered
  by the retry; the scan is cheap at this scale (hundreds of matches).
  *verification:* none required in-feature — a sequence/autoincrement redesign is
  out of scope and would be its own change; flagged here so it isn't forgotten.

## Out of scope (per spec non-goals)

No permissions tables / role hierarchy / admin-management UI; no change to
viewing; `GAME_ADMIN_EMAILS__*` untouched; no soft-delete; agent/connection
ownership unchanged.

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: Round 4 (converged). F1(email unique conflict) deferred-to-plan: email refresh must guard the users.email unique constraint (skip+log on collision; google_sub is the real key) — implementation detail for the plan. F2(backfill env parity) accepted: already a Risks verification item (confirm PLATFORM_ADMIN_EMAILS present at Alembic runtime; note legacy ADMIN_EMAILS fallback when asserting promoted row count). F3(admins cancel ACTIVE only via new route) accepted-by-design: the new role-based /matches/{id}/cancel IS the admin cancel path and the dashboard retargets to it; legacy /api/admin + /api/game-admin cancel stay 409-on-ACTIVE unchanged per AC-7. Residuals(cascade order unverifiable here, env parity) carried to plan checkpoint where admin_web.py + the shared helper are in-context.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: Round 4 (converged). HIGH1(delete-cascade winner_player_id order) accepted-as-documented: spec mandates verbatim extraction (no rewrite) + a shared-delete regression test; existing test_admin_delete_completed_match_with_winner already pins the order. HIGH2(_next_match_id full-scan perf) accepted-correctness/deferred-perf: IntegrityError retry covers PK-collision correctness; full-scan perf is a pre-existing small-scale concern (hundreds of matches) — a sequence/autoincrement redesign is out of scope. MED1(migration interruption/env) accepted: one Alembic revision = one transaction (column add + UPDATE atomic); env-presence is a Risks verification item. MED2(hybrid platform-role/game-email admin) accepted-by-design: game-admin email mechanism is an explicit non-goal; chrome shows platform vs game controls independently as today. LOW(email change vs GAME_ADMIN_EMAILS) accepted-edge: game-admin allowlist is operator-managed env (non-goal); a rare email change needs an env update, same as today.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: accepted | note: F1 HIGH(revocation delay) accepted-by-design: settled decision — role seeded at login, demotion at next login; documented in spec Risks; PLATFORM_ADMIN_EMAILS is operator-controlled and only takes effect on redeploy/restart either way; immediate per-request revocation was not a requirement. F2 MED(legacy ADMIN_EMAILS = 2nd source) accepted-by-design: feature consumes settings.platform_admin_emails_set (incl. its existing ADMIN_EMAILS fallback) for BOTH the migration backfill and login seeding, so users.role exactly mirrors the prior allowlist semantics — no new divergence; deprecating ADMIN_EMAILS is a non-goal. F3 MED(email lowercase) accepted -> tasks.md: seed via user.email.lower() in settings.platform_admin_emails_set (the set is already lowercased; matches the deps.py/web_support.py convention). Residual(cap counts scheduled?) accepted -> tasks.md states the cap counts state IN (SCHEDULED, REGISTERING, ACTIVE) per spec FR-5. Residual(route/template wiring unverified) noted: those files do not exist yet; the diff checkpoint verifies wiring.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: F1(ID race / missed caller) accepted-as-documented: reuse-report's #1 trap; plan mandates all five allocators converge on match_creation.py; tasks.md enumerates each caller's refactor + a per-path test that the creator is recorded (proving it routes through the helper). F2(cascade extraction thread-safety) accepted-as-documented: verbatim move (no rewrite) + Slice 3b regression test reproducing the in-flight-submission / second-sweep scenario; same logic regardless of caller. F3(email unique constraint) accepted: plan already guards (skip+log on collision); tasks.md adds the lowercase + the collision-skip test. Residual1(NOT NULL FK on existing rows) rejected/misread: created_by_user_id is NULLABLE by design — existing matches stay NULL, no default or data-migration needed. Residual2(ghost/over-privileged backfill) clarified: the backfill UPDATE only touches existing rows whose email is in the allowlist; a stale/typo email is a no-op (that admin is promoted at next login via sync), not a ghost admin or a lockout; the promoted-count verification catches a mismatch.
- review: reviews/diff.gemini.regression-adversarial.review.md | status: accepted | note: Slice 3b (shared delete cascade + cancel). All findings non-actionable. F1(delete_match atomicity) rejected/misread: the sequential db.execute(delete...) calls run in ONE implicit AsyncSession transaction committed atomically by the single db.commit() at the end — a mid-way exception never commits, so it rolls back, no partial state. Code moved VERBATIM from the proven admin_web route. F2(scheduler-teardown race) accept-as-documented: the two-pass submission sweep is the settled design for the cooperative-cancellation window (the in-code comment explains it). F3(no row-count verification) accept: original best-effort cascade behavior, the Match row delete is authoritative; out of scope. Also added cancel_match + converged 3 cancel sites (behavior-preserving, AC-7). Preflight green (650 tests). Expansion workaround applied.
