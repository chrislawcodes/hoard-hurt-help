---
reviewer: "gemini"
lens: "requirements-adversarial"
stage: "spec"
artifact_path: "docs/workflow/feature-runs/admin-user-management/spec.md"
artifact_sha256: "0446541506708c9815c2b2eda19de6bc4766c9dc6d6fbe3337121ccf9c34d765"
repo_root: "."
git_head_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
git_base_ref: "origin/main"
git_base_sha: "dd76f7929688b51f6ce0052722190eb530a69563"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "All findings are implementation-level (HOW), resolved in plan.md (not spec WHAT). HIGH content-negotiation: require_user is shared web+API — plan Decision 2 branches on Accept/path (303 web vs 403 JSON). HIGH N+1 on require_connection: plan specifies single query with join/select of owner disabled_at, no second SELECT. HIGH ConnectionSetup ordering: plan mandates disabled-owner check pre-empts the setup-token provisioning block. MEDIUM nav ghost-state: plan has get_current_user treat disabled as logged-out for nav. MEDIUM SQLite FOR UPDATE: plan Decision 5 corrected to dialect-branch (with_for_update only on non-sqlite). MEDIUM audit index: plan/migration adds index on target_user_id."
raw_output_path: "docs/workflow/feature-runs/admin-user-management/reviews/spec.gemini.requirements-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: spec requirements-adversarial

## Findings

*   **[CODE-CONFIRMED] HIGH: `require_user` 303 redirect mandate will break JSON API consumers.**
    *   **Spec:** FR-006 mandates that `require_user` enforces the disable check and that "Web routes redirect to a disabled-account notice page (303 → `/disabled`)".
    *   **Code:** `app/deps.py` shows `require_user` currently raises an `HTTPException(401)` containing a structured JSON error (`{"error": {"code": "NOT_SIGNED_IN"...}}`). This dependency is shared across both Web pages and API routes (e.g., `agent_api.py`, `admin_api.py`).
    *   **Flaw:** If `require_user` is changed to strictly return a `303 SEE OTHER` HTML redirect, all JSON API endpoints that rely on it will stop returning structured errors. API clients (like frontend `fetch` calls) will transparently follow the 303, receive the HTML of the `/disabled` page with a 200 OK, and crash trying to parse it as JSON. The dependency must use content negotiation (e.g., checking `Accept` headers or `request.url.path`) to branch between returning a 403 JSON error and a 303 HTML redirect.

*   **[CODE-CONFIRMED] HIGH: N+1 query penalty injected into the hottest path (`require_connection`).**
    *   **Spec:** FR-006a mandates that `require_connection` MUST "additionally reject when the owning user is disabled".
    *   **Code:** In `app/deps.py` (lines 188-195), `require_connection` currently only queries the `Connection` table. It does not fetch the `User` row.
    *   **Flaw:** To evaluate if the owning user is disabled, developers will be forced to either add an explicit `joinedload(Connection.user)` or perform a secondary `SELECT` against the `User` table. Because `require_connection` is the authentication gate for every single bot turn, this silently doubles the read load (or introduces a heavy join) on the platform's most heavily trafficked endpoint. The spec fails to account for this; `disabled_at` should ideally be denormalized to the `Connection` table or heavily cached.

*   **[CODE-CONFIRMED] HIGH: Disabled users can still mutate database state via `ConnectionSetup`.**
    *   **Spec:** FR-006a states disabled bots must be rejected via `require_connection`.
    *   **Code:** In `app/deps.py` (lines 198-219), if a connection doesn't exist, the code looks up a `ConnectionSetup` token, provisions a new `Connection` row, adds it to the DB, and calls `await db.flush()`.
    *   **Flaw:** If the user-disable check is evaluated against the `connection` object *after* it is resolved (which is where `deleted_at` and `status` checks currently live), a bot owned by a disabled user can still successfully consume a setup token, provision a new connection, and mutate the database before eventually receiving a 403. The disable check must explicitly pre-empt the `ConnectionSetup` processing block.

*   **[CODE-CONFIRMED] MEDIUM: Disabled users will appear as "logged in" on public UI.**
    *   **Spec:** FR-006 explicitly states: "The check lives in the web auth dependency (`require_user`... not in the pure getter `get_user_from_session` (which stays `-> User | None`)".
    *   **Code:** `app/deps.py` defines `get_current_user` which strictly wraps `get_user_from_session`. Global templates use `get_current_user` to populate the top navigation bar.
    *   **Flaw:** Because `get_user_from_session` ignores the disabled state, disabled users browsing public pages (like the homepage or spectator views) will still be served the "logged in" visual state. They will see their handle and a "My Agents" link in the nav bar, creating a confusing ghost state where the UI tells them they are logged in, but clicking anything results in a 303 redirect.

*   **[CODE-CONFIRMED] MEDIUM: SQLite `FOR UPDATE` lock requirement will break test suite.**
    *   **Spec:** FR-018 mandates `SELECT ... FOR UPDATE` (or the "SQLite-safe equivalent") to prevent race conditions during admin mutations. FR-014 requires the code to "apply cleanly on the SQLite test DB".
    *   **Code:** The app uses SQLAlchemy async sessions against both Postgres and SQLite.
    *   **Flaw:** SQLite does not support row-level `FOR UPDATE` locks. Attempting to use `with_for_update()` in SQLAlchemy against an SQLite backend will either emit invalid syntax or lock the entire database file depending on the dialect config, leading to deadlocks or failing tests. The spec assumes a drop-in SQLite equivalent exists for row-level concurrency, which is false. The code will require explicit branching logic (e.g., `if 'sqlite' not in db.bind.dialect.name: query = query.with_for_update()`).

*   **[UNVERIFIED] MEDIUM: Audit Log missing `target_user_id` index creates sequential scan hazard.**
    *   **Spec:** FR-014 defines `AdminAuditLog` with a foreign key on `target_user_id`, and FR-012 requires the user detail page to query these rows for display.
    *   **Code:** New schema to be generated by the developer.
    *   **Flaw:** The spec does not explicitly mandate an index on `target_user_id`. Without it, visiting `/admin/users/{user_id}` will trigger a full sequential scan of the append-only audit log table. As the audit log grows, loading the user detail page will degrade in performance.

## Residual Risks

*   **Zombie Bots Degrading Opponent Experience:** As explicitly acknowledged in the Non-Goals, disabling a user blocks their bot runners but does not remove their agents from active games or cancel queued matches. Because the bot can no longer authenticate, it will simply drop offline. Opponents in active matches will be forced to wait out the maximum turn timeout for every single round until the game concludes.
*   **Immutable Floor Compromise Latency:** The immutable config floor (FR-010) guarantees that users in `PLATFORM_ADMIN_EMAILS` cannot be disabled in-app. If a config admin's Google account is compromised, other admins have zero in-app recourse to stop them from wreaking havoc. Revocation requires a code change to `config.py`/environment variables and a full platform redeploy, increasing the mean time to mitigation for the most highly privileged accounts.

## Token Stats

- total_input=25453
- total_output=1522
- total_tokens=35668
- `gemini-3.1-pro-preview`: input=25453, output=1522, total=35668

## Resolution
- status: accepted
- note: All findings are implementation-level (HOW), resolved in plan.md (not spec WHAT). HIGH content-negotiation: require_user is shared web+API — plan Decision 2 branches on Accept/path (303 web vs 403 JSON). HIGH N+1 on require_connection: plan specifies single query with join/select of owner disabled_at, no second SELECT. HIGH ConnectionSetup ordering: plan mandates disabled-owner check pre-empts the setup-token provisioning block. MEDIUM nav ghost-state: plan has get_current_user treat disabled as logged-out for nav. MEDIUM SQLite FOR UPDATE: plan Decision 5 corrected to dialect-branch (with_for_update only on non-sqlite). MEDIUM audit index: plan/migration adds index on target_user_id.
