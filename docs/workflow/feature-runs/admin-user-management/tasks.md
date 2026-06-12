# Tasks: Platform Admin User Management

**Slug:** admin-user-management
**Plan:** docs/workflow/feature-runs/admin-user-management/plan.md

Each slice ends at a `[CHECKPOINT]` (build + tests green, commit, diff review for
slices ≥ 50 changed lines). Slices are ordered so each builds on the last.

---

## Slice 1 — Data model + migration  `[CHECKPOINT]`

Foundation: the column and table everything else depends on.

- [ ] T1.1 Add `disabled_at: Mapped[datetime | None]` to `User`
  (`app/models/user.py`), nullable, `DateTime(timezone=True)`, default NULL.
- [ ] T1.2 New `app/models/admin_audit_log.py`: `AdminAction` enum
  (`disable`, `enable`, `promote`, `demote`, `handle_reset`) via `FlexibleEnumType`;
  `AdminAuditLog` model — `id`, `actor_user_id` (FK `users.id`, `ondelete="RESTRICT"`,
  **indexed**), `target_user_id` (FK `users.id`, `ondelete="RESTRICT"`, **indexed**),
  `action`, `reason: str | None` (≤500), `created_at` server-default `func.now()`.
- [ ] T1.3 Register the model where models are imported for metadata (mirror how
  existing models are wired so `create_all`/autogenerate sees it).
- [ ] T1.3b `app/models/connection.py`: add `user: Mapped[User] = relationship("User")`
  (no `back_populates` needed; lazy load is fine since it is only loaded via explicit
  `joinedload` in `require_connection`).
- [ ] T1.4 Migration `migrations/versions/0029_admin_user_management.py`, down_revision
  `0028`: add `users.disabled_at` (batch mode); create `admin_audit_log` table +
  index on `target_user_id` + index on `actor_user_id`. Mirror `0028_user_roles.py`
  batch_alter_table style.
- [ ] T1.5 Tests: model imports; `tests/test_migrations.py`-style upgrade applies
  on SQLite test DB; audit FKs are `RESTRICT`; `target_user_id` index exists.
- [ ] T1.6 Preflight (`ruff`, `mypy`, `pytest -q`); commit. `[CHECKPOINT]`

DO NOT TOUCH: routes, templates, deps. ~130 lines.

---

## Slice 2 — Disable enforcement (both auth paths) + additive login-sync  `[CHECKPOINT]`

The security core. Implements Decisions 2, 3, 7, 8.

- [ ] T2.1 `app/auth/session.py`: add `raise_account_disabled(request)` — 3-branch:
  `HX-Request` → 200 + `HX-Redirect: /disabled`; `Accept: text/html` (no HX) →
  303 → `/disabled`; else → 403 JSON `ACCOUNT_DISABLED` (envelope like `NOT_SIGNED_IN`).
- [ ] T2.2 `app/deps.py` `require_user`: after loading the user, if `disabled_at`
  is set → `raise_account_disabled(request)`. `get_current_user` is UNCHANGED
  (still returns the `User`, incl. disabled — do NOT return None).
- [ ] T2.3 `app/deps.py` `require_connection`: live-connection lookup uses
  `select(Connection).options(joinedload(Connection.user).load_only(User.disabled_at))`
  (returns a Connection scalar — NOT a tuple); check `connection.user.disabled_at`
  alongside `deleted_at`/`PAUSED` → 403 `ACCOUNT_DISABLED`. `.load_only` avoids
  fetching the full User row on the hot agent-poll path. In the `ConnectionSetup`
  branch, BEFORE building the `Connection`/`db.flush()`, run
  `select(User.disabled_at).where(User.id == setup.user_id)` → 403 if disabled.
- [ ] T2.4 Public `GET /disabled` route (in `web_lobby.py` or nearest public web
  router) + `app/templates/disabled.html` notice page.
- [ ] T2.5 `app/routes/auth.py`: make `sync_google_user` role assignment additive
  (config email → ensure ADMIN; else leave `user.role` unchanged). Google callback
  sets the session as today, then if the user is disabled redirect to `/disabled`.
- [ ] T2.6 `app/templates/base.html`: when `user.disabled_at` is set, show an
  "account disabled" nav state instead of the normal account links (CTA buttons
  and connection counts hidden). Guard the check as `{% if user and user.disabled_at %}`
  — `user` is `None` for anonymous visitors and a Jinja `UndefinedError` would crash
  public pages otherwise.
- [ ] T2.7 Tests: web route → 303; HTMX request → `HX-Redirect`; API route →
  403 JSON; connection key on `next_turn`/`next_turns`/`report_pid` → 403
  `ACCOUNT_DISABLED`; setup token by a disabled owner creates NO Connection row;
  disabled-but-signed-in user reaches `/disabled` (no login loop) and
  `get_current_user` returns them; promoted-in-app user stays `ADMIN` after a
  re-login; config email forced to `ADMIN`.
- [ ] T2.8 Preflight; commit. `[CHECKPOINT]`

~240 lines.

---

## Slice 3 — Mutation service + endpoints (disable/enable/promote/demote)  `[CHECKPOINT]`

Implements Decisions 4, 5; FR-011/016/018.

- [ ] T3.1 New `app/services/admin_user_actions.py`: helpers for disable, enable,
  promote, demote, handle_reset. Each: load target (dialect-aware
  `with_for_update` via `db.get_bind().dialect.name != "sqlite"`); no-op rule
  (already in target state / handle reset with no handle → return, no audit row);
  floor refusal (`target.email.lower() in platform_admin_emails_set` → refuse
  demote AND disable); mutate + write one `AdminAuditLog` row in the same txn.
- [ ] T3.2 `app/routes/admin_web.py`: POST handlers for disable/enable/promote/
  demote, gated by `require_platform_admin`, calling the service helpers.
- [ ] T3.3 Retrofit existing `admin_reset_handle` to route through the service
  (no-op + no audit row when the target has no handle).
- [ ] T3.4 Tests: each action writes exactly one audit row; no-op writes none;
  floor email refused for demote AND disable (case-insensitive); non-admin → 403;
  handle_reset on a handle-less user is a no-op with no row.
- [ ] T3.5 Preflight; commit. `[CHECKPOINT]`

~220 lines.

---

## Slice 4 — Admin UI (list + detail + badges + nav)  `[CHECKPOINT]`

Implements US1/US2; FR-001/002/003/015/016.

- [ ] T4.1 `GET /admin/users` (`admin_web.py`, `require_platform_admin`):
  paginated (page size 50, `.offset()/.limit()`), optional `q` →
  `email ILIKE %q% OR handle ILIKE %q%` (case-insensitive). Show email, handle,
  created date, agent count, admin badge, disabled badge.
- [ ] T4.2 `app/templates/admin/users_list.html`.
- [ ] T4.3 `GET /admin/users/{user_id}` detail: connections (provider, status),
  agents (name, status), recent matches (cap 20), role, disabled state, audit
  history (bounded) newest-first. `app/templates/admin/user_detail.html` with the
  action buttons (disable/enable/promote/demote) posting to Slice-3 endpoints.
- [ ] T4.4 Add disabled/admin badges to the existing `/admin/handles` view
  (`app/templates/admin/handles.html`).
- [ ] T4.5 Add the "Users" link to the admin nav block in `base.html`.
- [ ] T4.6 Tests: list renders + `q` filters by email and handle; detail shows
  owned entities + audit; non-admin → 403 on both routes.
- [ ] T4.7 Preflight; commit. `[CHECKPOINT]`

~200 lines.

---

## Slice 5 — Startup warning + STATUS  `[CHECKPOINT]`

Implements FR-017.

- [ ] T5.1 `app/main.py` lifespan: explicitly evaluate
  `settings.platform_admin_emails_set`; if empty, log a loud warning (advisory,
  non-blocking) — mirror `_check_oauth_config()`. Fires on the RESOLVED set
  (so a lingering `ADMIN_EMAILS` counts as non-empty).
- [ ] T5.2 Test: warning logged when the resolved set is empty; silent otherwise.
- [ ] T5.3 Update `STATUS.md` for what shipped.
- [ ] T5.4 Preflight; commit. `[CHECKPOINT]`

~40 lines.

---

## Parallelization

Slices are sequential (each depends on the prior): Slice 2 needs Slice 1's model;
Slices 3–4 need Slice 2's enforcement + helpers. No safe cross-slice `[P]`
parallelism. Within Slice 4, the list (T4.1/4.2) and detail (T4.3) pages touch
different templates but share `admin_web.py`, so keep them serial to avoid a
same-file conflict.

## Global guardrails

DO NOT MODIFY: `CLAUDE.md`, `AGENTS.md`, `MEMORY.md`, `.gitignore`, the spec/plan,
or any file outside the scope paths. No `# type: ignore` / `# noqa` / swallowed
exceptions. All new functions fully type-annotated. Preflight must be green before
each checkpoint commit.
