---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/admin-user-management/plan.md"
artifact_sha256: "68ae847e299f8135ab83b09dc9b987d31b84e8ef09a1a813cdf88deaed36a4d9"
repo_root: "."
git_head_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
git_base_ref: "origin/main"
git_base_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "HIGH#1 revocation gap: documented 2-step process (remove config + second admin demotes); additive sync is intentional for durability. HIGH#2 load_only: T2.3 updated to use .load_only(User.disabled_at). HIGH#3 content negotiation: rejected — plan T2.1 'else → 403 JSON' already covers Accept:*/* correctly; 303 only on explicit Accept:text/html. MEDIUM#4 Jinja guard: T2.6 updated with {% if user and user.disabled_at %}. MEDIUM#5 actor index: T1.2/T1.4 updated to add actor_user_id index. LOW#6 db.get_bind(): plan is correct — AsyncSession proxies get_bind() to sync session; db.bind was removed in SQLAlchemy 2.x so Gemini's suggestion is invalid for our version."
raw_output_path: "docs/workflow/feature-runs/admin-user-management/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

**HIGH: Severe Revocation Gap for Config Admins due to Additive Sync** `[CODE-CONFIRMED]`
The plan introduces a dangerous security loophole by combining Decision 3 ("Additive login-sync: preserve stored role") and Decision 4 ("Config admins are FULLY immutable in-app"). The plan states the emergency mitigation for a rogue config admin is to "remove from `PLATFORM_ADMIN_EMAILS` + redeploy". However, because `sync_google_user` is now additive, removing them from the config floor does *not* strip their `ADMIN` role in the database. After the redeploy, the rogue actor's active session remains entirely valid, and they retain full `ADMIN` privileges in the DB until a *second* admin manually logs in and demotes them. The rogue admin is never forcefully severed.

**HIGH: Performance Bloat on the Hottest Polling Path** `[CODE-CONFIRMED]`
Decision 7 mandates `select(Connection).options(joinedload(Connection.user))` in `require_connection` to avoid returning a tuple. According to `AGENT_LUDUM_ARCHITECTURE.md`, `agent_next_turn.py` is a high-frequency polling endpoint ("The heart of paste-once play"). Using an unfiltered `joinedload` forces the database and ORM to fetch and hydrate the entire `User` row (including `email`, `role`, `handle`, etc.) on every single agent poll just to check the `disabled_at` boolean. This will cause severe memory and network bloat under load. It must use `.options(joinedload(Connection.user).load_only(User.disabled_at))`.

**HIGH: Content Negotiation in `require_user` Will Break API Clients** `[CODE-CONFIRMED]`
Decision 2 introduces content negotiation (`raise_account_disabled`) into `require_user`, returning a 303 Redirect for HTML and a 403 for JSON/API. `AGENT_LUDUM_ARCHITECTURE.md` shows that `require_user` (and `require_platform_admin` which wraps it) guards explicit JSON APIs like `game_admin_api.py`. Automated API clients (like `curl` or python `requests`) often omit strict `Accept: application/json` headers, defaulting to `*/*`. If the content negotiation falls back to HTML for generic accept headers, programmatic clients will silently receive a 303 Redirect to `/disabled` (which returns a 200 HTML page) instead of the expected 403 JSON, breaking error handling.

**MEDIUM: Jinja Crash on Public Pages** `[CODE-CONFIRMED]`
Decision 8 states "nav branches on `user.disabled_at`" in `base.html`. `reuse-report.md` confirms `get_user_from_session` signature remains `-> User | None`. If the template literally branches via `{% if user.disabled_at %}`, any unauthenticated visitor (where `user` is `None`) will trigger a Jinja `UndefinedError` / `AttributeError`, crashing all public pages (like the front page and leaderboard). The check must be strictly guarded: `{% if user and user.disabled_at %}`.

**MEDIUM: Missing Audit Index for Actor Accountability** `[CODE-CONFIRMED]`
Slice 1 and Decision 9 explicitly dictate adding an index to `admin_audit_log.target_user_id` but omit an index for `actor_user_id`. In an adversarial incident response scenario where an admin account is compromised, the primary investigative query is "What actions did this actor perform?" (`WHERE actor_user_id = ?`). Without this index, the query will force a sequential scan on an append-only table.

**LOW: Invalid Async Dialect Accessor** `[UNVERIFIED]`
Decision 5 forcefully dictates using `db.get_bind().dialect.name` (and explicitly rejects `db.bind`). In SQLAlchemy `ext.asyncio`, calling `AsyncSession.get_bind()` is a synchronous operation that raises an `InvalidRequestError` if called directly inside an async route without a `run_sync` context block. The correct, async-safe way to check the dialect on an async session's engine is `db.bind.dialect.name`.

## Residual Risks

*   **Lingering Active Sessions:** Because the system does not have a session revocation table, setting `disabled_at` relies exclusively on `require_user`/`require_connection` catching it on the next request. If a new route is accidentally added without these dependencies, a disabled user with an unexpired session cookie will have full access to that endpoint. 
*   **Race Conditions on SQLite:** While the plan assumes SQLite connection-level serialization prevents mutation races (Decision 5), concurrent requests using `ext.asyncio` with SQLite can still result in `OperationalError: database is locked` timeouts if the single transaction lock is held during a slow audit write. The lack of `SELECT ... FOR UPDATE` means the application must rely strictly on SQLite's file-level locking behavior.

## Token Stats

- total_input=28588
- total_output=1091
- total_tokens=38992
- `gemini-3.1-pro-preview`: input=28588, output=1091, total=38992

## Resolution
- status: accepted
- note: HIGH#1 revocation gap: documented 2-step process (remove config + second admin demotes); additive sync is intentional for durability. HIGH#2 load_only: T2.3 updated to use .load_only(User.disabled_at). HIGH#3 content negotiation: rejected — plan T2.1 'else → 403 JSON' already covers Accept:*/* correctly; 303 only on explicit Accept:text/html. MEDIUM#4 Jinja guard: T2.6 updated with {% if user and user.disabled_at %}. MEDIUM#5 actor index: T1.2/T1.4 updated to add actor_user_id index. LOW#6 db.get_bind(): plan is correct — AsyncSession proxies get_bind() to sync session; db.bind was removed in SQLAlchemy 2.x so Gemini's suggestion is invalid for our version.
