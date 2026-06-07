---
reviewer: "gemini"
lens: "testability-adversarial"
stage: "plan"
artifact_path: "docs/workflow/feature-runs/admin-role-split/plan.md"
artifact_sha256: "3f475ac9097cf8344ac479e58b9f231aa4571b98690d7640162877bf49b2b55c"
repo_root: "."
git_head_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
git_base_ref: "origin/claude/awesome-bohr-fBDnG"
git_base_sha: "e9c316bb0db5f017c9ae0dc55f2af8bcbe7f576a"
generation_method: "gemini-cli"
resolution_status: "accepted"
resolution_note: "HIGH(require_game_admin on wrong route) — fixed: verification grep added. HIGH(template hardcoded paths) — fixed: step 8 audits base.html and static/. MEDIUM(os.environ scan) — fixed: _is_any_admin delegates to settings.all_game_admin_emails_set, built once at startup."
raw_output_path: "docs/workflow/feature-runs/admin-role-split/reviews/plan.gemini.testability-adversarial.review.md.json"
narrowed_artifact_path: ""
narrowed_artifact_sha256: ""
coverage_status: "full"
coverage_note: ""
---

# Review: plan testability-adversarial

## Findings

1.  **[CODE-CONFIRMED] Dependency Injection Mismatch:** The plan defines `require_game_admin` as a dependency that requires a `game: str = Path(...)` argument. However, `app/routes/game_admin_web.py` needs to ensure the `game` matches the `match.game` for resource-specific requests (e.g., `/games/{game}/admin/matches/{match_id}`). The current plan implies the dependency only verifies admin status for that game but relies on handler-level 404 checks for cross-game isolation. This creates a risk where a valid "Game Admin" for Game A could access a match in Game B because the dependency only checked the `game` path param and not the `match_id` ownership until the handler execution, which is prone to boilerplate errors.

2.  **[CODE-CONFIRMED] Incomplete Deprecated Cleanup:** The plan states "Remove `require_admin` from `app/deps.py`" in step 13. However, it does not explicitly account for all internal references or potential dynamic lookups. While the grep check at the end mitigates this, the plan lacks an explicit step to audit `app/routes/admin_api.py` and other internal helpers that might implicitly depend on the old `require_admin` logic beyond mere imports.

3.  **[UNVERIFIED] Template URL Fragility:** The migration step for templates (`app/templates/game_admin/`) assumes a simple search-and-replace of `/admin/` with `/games/{game}/admin/`. The codebase uses Jinja2 with complex URL generation, and some references may be dynamically constructed in JS or partials not covered by a simple grep.

4.  **[CODE-CONFIRMED] Inefficient `_is_any_admin` Implementation:** The plan suggests `_is_any_admin` should scan `os.environ` via `settings.all_game_admin_emails_set` on every request. While `settings` is `lru_cache`'d, the property itself performs a dictionary scan and string processing on every call. This is unnecessary overhead for a template nav-visibility helper.

## Residual Risks

1.  **Router Registration Conflict:** Modifying `app/main.py` to register all routers while simultaneously updating `tests/conftest.py` carries a risk of circular dependencies or duplicate mounting if the teardown of the old wiring is not atomic.
2.  **Auth Inconsistency:** If `PLATFORM_ADMIN_EMAILS` and `GAME_ADMIN_EMAILS__*` overlap or are misconfigured, the order of precedence in `_is_any_admin` and the separate `require_*` dependencies could lead to confusing UX if a platform admin cannot perform a game-specific admin action, or vice-versa, due to strict enforcement boundaries.
3.  **Migration Scope:** Migrating the templates and logic "verbatim" assumes that the legacy code was perfectly scoped and had no hard dependencies on the platform-level `admin_web` state, which may not hold true in practice.

## Token Stats

- total_input=25067
- total_output=668
- total_tokens=25735
- `gemini-3.1-flash-lite`: input=25067, output=668, total=25735

## Resolution
- status: accepted
- note: HIGH(require_game_admin on wrong route) — fixed: verification grep added. HIGH(template hardcoded paths) — fixed: step 8 audits base.html and static/. MEDIUM(os.environ scan) — fixed: _is_any_admin delegates to settings.all_game_admin_emails_set, built once at startup.