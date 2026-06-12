# Reuse Audit — Platform Admin User Management

Read-only audit of the existing codebase to decide, per capability, whether the
feature should **reuse**, **extend**, or build **justified-new**. Bias is toward
reuse/extend.

| capability | existing module (path) | verdict | note |
|---|---|---|---|
| 1. Admin user list w/ pagination + case-insensitive substring search | `app/routes/admin_web.py:77` (`admin_handles`) is the closest list page; `app/routes/web_lobby.py:300` (`leaderboard_page`) for the page-shape/template pattern | **extend (page pattern) + justified-new (pagination/search query)** | `admin_handles` already does the exact `select(User)...order_by(...)` + `templates/admin/handles.html` shape — mirror it. BUT **no paginated query exists anywhere**: `grep` finds **zero** `.offset(` calls and no `ilike`/`func.lower` substring search in `app/`. So the `LIMIT/OFFSET` + `ilike('%q%')` (or `func.lower(...).contains`) query in FR-001 is genuinely new code. Reuse the route/template scaffolding; write the query fresh. Agent-count per row = the `select(func.count()).select_from(Agent).where(Agent.user_id==...)` pattern already in `auth.py:116`. |
| 2. Audit log of admin actions | `app/models/request_incident.py` (`RequestIncident`) + `app/request_logging.py` | **justified-new** (do NOT extend `RequestIncident`) | See "Key decisions" — this is the big call. The incident table is an error/500 capture log, wrong shape for action auditing. New `AdminAuditLog` model + table is justified. |
| 3. Disable enforcement at connection/runner auth | `app/deps.py:139` `require_connection` — `CONNECTION_DELETED` (410, `:215`) and `CONNECTION_PAUSED` (403, `:226`) blocks | **extend** | Add an `ACCOUNT_DISABLED` 403 block mirroring `CONNECTION_PAUSED` exactly (same `HTTPException` + `detail={"error":{"code":..,"message":..,"details":{}}}` shape). `Connection.user_id` is already on the loaded `connection` object; add a `User` lookup (or join) and reject when `user.disabled_at is not None`. Place the check **after** the pause/delete blocks and **before** `mark_seen` so a disabled owner's runner can't even heartbeat. |
| 4. Disable enforcement at web auth | `app/deps.py:35` `require_user`; `app/deps.py:51` `require_user_with_handle` (303 to `/me/handle?next=...`) | **extend** | `require_user_with_handle:64` is the exact 303-redirect pattern to mirror — raise `HTTPException(303, headers={"Location": "/disabled"})`. Spec (FR-006) says put the disabled check in `require_user` itself (or a wrapper), NOT in the pure getter `get_user_from_session` (`app/auth/session.py:12`, stays `-> User | None`). Session is DB-backed (`session.py` stores only `user_id`, re-fetches `User` every request) so the check bites on the very next request. |
| 5. Role promote/demote mutation | `app/routes/auth.py:77` (`sync_google_user`) | **justified-new (helper) + extend (sync_google_user)** | `auth.py:77` (`user.role = role`) is the **only** writer of `user.role` in the whole app (`grep` confirms — every other hit is a read/compare). There is no reusable role-mutation helper, so the promote/demote endpoints write new code. Separately, `sync_google_user` MUST be **modified** (extend) per FR-009: make it additive — set `ADMIN` for floor emails, otherwise **preserve** the stored role instead of forcing `USER` (current `:77` clobbers). |
| 6. Public `/disabled` notice route | `app/routes/web_lobby.py:300` `leaderboard_page` / `web_lobby.py:220` front page — minimal public GET + `templates.TemplateResponse` | **reuse (pattern)** | Mirror any public GET in `web_lobby.py`: no auth dep, `get_current_user` optional, render a small template. Add the route to `admin_web.py` is wrong (it's public) — put it in a human web router (e.g. `web_lobby.py` or `handle_web.py`-style) so it's reachable while signed-in-but-disabled. |
| 7. Admin nav + templates + sub-page pattern | `app/templates/base.html:84` (`{% if is_admin %}<a href="/admin">`), `app/templates/admin/` (`dashboard/handles/incidents/incident_detail.html`), `app/routes/admin_web.py` | **extend / reuse** | Pattern is established: a route in `admin_web.py` guarded by `Depends(require_platform_admin)`, returning `templates.TemplateResponse(request, "admin/<page>.html", {"user":.., "is_admin": True, ..})`. Add `/admin/users` + `/admin/users/{id}` here, new `admin/users*.html` templates. FR-015 nav entry: add a "Users" link in `admin/dashboard.html` (the admin landing already lists sub-pages); the top-nav `is_admin` link at `base.html:84` stays pointing at `/admin`. `_is_any_admin` lives at `app/routes/web_support.py:47`. |
| 8. Alembic migration: add column + table, SQLite batch-safe | `migrations/versions/0028_user_roles.py` (current **head**, revision id `0028`) | **reuse (pattern)** | `0028` is the model: `with op.batch_alter_table("users") as batch_op: batch_op.add_column(...)` for the column, plus FK/index ops inside `batch_alter_table`. New migration chains `down_revision = "0028"`. Add `users.disabled_at` (nullable `DateTime(timezone=True)`) via `batch_alter_table`; `op.create_table("admin_audit_log", ...)` for the new table with FKs to `users.id` using `ondelete="RESTRICT"` (FR-014). Constraint ops on SQLite **must** be inside `batch_alter_table` (project rule; `tests/test_migrations.py` guards the up/down round-trip). |
| 9. Startup/lifespan hook for empty-PLATFORM_ADMIN_EMAILS warning (FR-017) | `app/main.py:143` `lifespan` (and the existing `_check_oauth_config()` at `main.py:69`, called at `:145`) | **extend** | `_check_oauth_config()` is the exact precedent: a module-level `def _check_*()` that logs a `logger.warning(...)` and is invoked first thing inside `lifespan`. Add a sibling `_check_platform_admin_floor()` that warns when `settings.platform_admin_emails_set` is empty, called right after `_check_oauth_config()`. Advisory only, does not block boot — matches FR-017 and the project's "fail-open: advisory only" rule. The set property is at `app/config.py:98`. |

## Key decisions

### Audit log: justified-new, do NOT extend `RequestIncident` (recommendation)

**Recommendation: build a new `AdminAuditLog` model + table. Do not reuse or
extend `request_incidents`.**

I investigated this hard because it's the spec's biggest open question. The
existing infra is `RequestIncident` (`app/models/request_incident.py`) written by
`app/request_logging.py`. Here is why it does not fit, despite both being
"a log table":

**What `RequestIncident` actually is.** It is an **error-capture / 500 log**. Its
columns: `request_id`, `method`, `path`, `query_string`, `user_id`, `match_id`,
`bot_id`, `player_id`, `stage`, **`error_type`, `error_message`, `stacktrace`**,
`context_json`, `created_at`. It is written in exactly two places, both
failure-only:
- `request_logging.py:199` — inside the middleware's `except Exception` arm, only
  when a request **crashes** (HTTP 500).
- `record_background_incident` (`:118`) — when a background task crashes.

It is surfaced at `/admin/incidents` as a debugging tool ("here's the 500 you
hit, with its stacktrace"). There is no success path that writes it.

**Why it's the wrong shape for admin-action auditing.**

| Need (FR-011) | `RequestIncident` | Fit |
|---|---|---|
| `actor_user_id` (who did it) | only `user_id` (single subject) | no — can't express actor vs target |
| `target_user_id` (to whom) | none | no |
| `action` enum (disable/enable/promote/demote/handle_reset) | none — closest is `error_type`/`stage` (free strings about a crash) | no |
| `reason` (free text ≤500) | none (`error_message` is the exception text) | no |
| written on **success** | written only on **failure** (500/crash) | fundamentally opposite |
| append-only accountability record | a debugging dump w/ stacktrace | wrong purpose |
| FK `ON DELETE RESTRICT` to users (audit must survive) (FR-014) | `user_id` is a bare `Integer`, **not even a FK** | no |

Forcing `RequestIncident` to carry admin actions would mean overloading
`error_type`/`stage`/`error_message` to mean "action"/"target"/"reason",
abandoning the structured `actor/target/action` schema the spec requires, mixing
crash dumps with deliberate privilege changes in one `/admin/incidents` view, and
losing the `ON DELETE RESTRICT` integrity FR-014 calls for. That is a worse
outcome than a small purpose-built table.

**What to reuse instead.** Reuse the *conventions*, not the table:
- The `created_at` server-default pattern (`DateTime(timezone=True),
  server_default=func.now()`) straight from `request_incident.py:32`.
- The `FlexibleEnumType` pattern (`app/models/user.py:27`, used across
  `app/models/`) for the `action` enum column, with a `server_default`.
- The admin list/detail render pattern from `admin_web.py` (`/admin/incidents`
  → `/admin/users/{id}` audit section, newest-first `order_by(created_at.desc())`
  + `limit` — same as `admin_incidents` at `admin_web.py:127`).

So `AdminAuditLog` is genuinely new, but it should *look* like the existing
models and its read path should *look* like `/admin/incidents`. Scope it exactly
as the spec says — admin user-management actions only, not platform-wide
auditing (that's an explicit non-goal).

### Other notable reuse points

- **`require_platform_admin` (`app/deps.py:71`) is ready as-is** — it already
  gates on `user.role == UserRole.ADMIN`. Every new `/admin/users*` endpoint just
  takes `Depends(require_platform_admin)`. The `User.role` / `UserRole` enum
  (`app/models/user.py:13,27`) and the config floor
  (`settings.platform_admin_emails_set`, `config.py:98`) all already exist — the
  feature's "role column" decision is already satisfied.
- **The 303-redirect mechanism for `/disabled` is a copy of
  `require_user_with_handle`** (`app/deps.py:51-68`) — same `HTTPException(303,
  headers={"Location": ...})` shape, just a fixed target.
- **The connection-path rejection is a copy of `CONNECTION_PAUSED`**
  (`app/deps.py:226-236`) — identical JSON-403 error envelope, new code
  `ACCOUNT_DISABLED`.
- **The migration is a copy of `0028`'s `batch_alter_table` usage** and chains
  off it as the current head.
- **The startup warning is a copy of `_check_oauth_config`** (`app/main.py:69`,
  invoked at `:145`).

### Net verdict tally

- **reuse**: #6 (public route pattern), #8 (migration pattern)
- **extend**: #3, #4, #7, #9; plus `sync_google_user` for #5
- **justified-new**: #2 (`AdminAuditLog`), the paginated/search **query** in #1,
  the role-mutation **endpoints/helper** in #5

The only fully justified-new piece of substance is the `AdminAuditLog` table and
the offset+ilike list query — everything else mirrors an existing pattern closely.
