# Plan: Split Admin into Platform Admin and Game Admin

**Slug:** admin-role-split
**Branch:** claude/awesome-bohr-fBDnG

## Review Reconciliation

- review: reviews/spec.codex.feasibility-adversarial.review.md | status: accepted | note: MEDIUM(rolling deploy) — Railway single-instance, no mixed-version pods. MEDIUM(pydantic parsing) — plan will verify. MEDIUM(fails open) — compat window intent; removed after env vars confirmed.
- review: reviews/spec.gemini.requirements-adversarial.review.md | status: accepted | note: [UNVERIFIED](slug normalization) — out of scope. (path param injection) — 403, not a security risk. (non-path auth) — accepted. (ADMIN_EMAILS lifecycle) — single-instance.
- review: reviews/plan.codex.implementation-adversarial.review.md | status: insufficient | note: Codex runner timed out — no findings generated. Claude performed manual implementation review: no additional blockers found beyond what Gemini flagged.
- review: reviews/plan.gemini.testability-adversarial.review.md | status: accepted | note: HIGH(require_game_admin on wrong route) — fixed: added verification grep + explicit constraint note that ALL game admin routes have {game} in path by design. HIGH(template hardcoded paths) — fixed: step 8 now includes grep audit of base.html and static/ for /admin/ references. MEDIUM(os.environ scan per request) — fixed: _is_any_admin delegates to settings.all_game_admin_emails_set, which reads from _game_admin_emails_raw populated once at Settings construction.

## Architecture Decisions

### Decision 1: pydantic-settings env-prefix dict support

pydantic-settings v2 supports `env_nested_delimiter` to parse nested dicts from env vars,
but the `__` delimiter conflicts with the regular env prefix feature. Instead, use a
`@model_validator(mode='before')` or a plain `@classmethod` that scans `os.environ` for
keys matching `GAME_ADMIN_EMAILS__*` and builds the dict manually. This is simpler and
more portable than relying on pydantic-settings internals.

Implementation: Add `game_admin_emails_raw: dict[str, str] = {}` populated by a
`@model_validator(mode='before')` that iterates `os.environ` and collects matching keys.

### Decision 2: _is_admin() replacement strategy

`_is_admin()` in web_support.py is used in six non-admin route modules (viewer, lobby,
player pages, etc.) purely to drive the admin nav link in the base template. After the
split there are two admin roles. Rather than threading two flags through every route:
- Replace `_is_admin()` with `_is_any_admin(user)` that returns true if user is platform
  admin OR game admin for any game. Nav display is unchanged for existing admins.
- In web_viewer.py only: additionally compute and pass `is_game_admin` (game-specific)
  to control strategy_text display (AC 7).

This keeps the scope targeted — security enforcement lives in the deps, not the nav helper.

### Decision 3: Admin router wiring in main.py

Currently `create_app()` registers only agent-API and connections routes. The web and
admin routes are added in `tests/conftest.py`. This feature adds the platform-admin and
game-admin routers into `create_app()` alongside the other routers. conftest.py will be
updated to NOT re-register those routers (it mounts the live `app` object, so the
routers added in `create_app()` are already present).

### Decision 4: admin_api.py fate

All four routes in admin_api.py are game-level. After this split they move to
game_admin_api.py. admin_api.py will be deleted (not left empty) — an empty router file
is misleading. The conftest.py import will be removed alongside.

## Files Changed

| File | Change |
|------|--------|
| `app/config.py` | Add `platform_admin_emails`, `game_admin_emails_raw`, compat fallback |
| `app/deps.py` | Add `require_platform_admin`, `require_game_admin`; remove `require_admin` |
| `app/routes/web_support.py` | Rename `_is_admin` → `_is_any_admin`; add `_is_game_admin` |
| `app/routes/admin_web.py` | Remove game-level routes + templates; swap to `require_platform_admin` |
| `app/routes/admin_api.py` | **DELETE** (all routes move to game_admin_api) |
| `app/routes/game_admin_web.py` | **NEW** — game-scoped web admin routes |
| `app/routes/game_admin_api.py` | **NEW** — game-scoped API admin routes at `/api/game-admin/{game}` |
| `app/templates/admin/*.html` | Remove 4 game-scoped templates (after copying to game_admin/) |
| `app/templates/game_admin/` | **NEW dir** — 4 templates migrated from admin/ |
| `app/routes/web_viewer.py` | Pass `is_game_admin` for strategy_text gating |
| `app/routes/web_lobby.py` | `_is_admin` → `_is_any_admin` |
| `app/routes/web_player.py` | `_is_admin` → `_is_any_admin` |
| `app/routes/web_analysis.py` | `_is_admin` → `_is_any_admin` |
| `app/routes/handle_web.py` | `_is_admin` → `_is_any_admin` |
| `app/main.py` | Wire platform-admin + game-admin routers into `create_app()` |
| `tests/conftest.py` | Remove admin router imports + include_router calls |
| `tests/test_admin.py` | Update test setup; add boundary tests |
| `tests/test_admin_add_sims.py` | Update test setup |

## Detailed Changes

### 1. app/config.py

```python
# New fields:
platform_admin_emails: str = Field(default="")
# Old field preserved for compat:
admin_emails: str = Field(default="")

# New property:
@property
def platform_admin_emails_set(self) -> set[str]:
    """Platform admins. Falls back to admin_emails if not set (compat window)."""
    raw = self.platform_admin_emails or self.admin_emails
    if not raw:
        return set()
    if raw != self.platform_admin_emails:
        import logging
        logging.getLogger(__name__).warning(
            "ADMIN_EMAILS fallback active — set PLATFORM_ADMIN_EMAILS to remove"
        )
    return {e.strip().lower() for e in raw.split(",") if e.strip()}

# New validator + method for game admin emails:
@model_validator(mode='before')
@classmethod
def _collect_game_admin_emails(cls, values):
    """Scan os.environ for GAME_ADMIN_EMAILS__* keys and build the dict."""
    import os
    prefix = "GAME_ADMIN_EMAILS__"
    result = {}
    for k, v in os.environ.items():
        if k.upper().startswith(prefix):
            slug_key = k[len(prefix):]  # e.g. "HOARD_HURT_HELP"
            result[slug_key] = v
    values["_game_admin_emails_raw"] = result
    return values

# Store the raw dict (not exposed directly as a field):
_game_admin_emails_raw: dict[str, str] = Field(default_factory=dict)

def game_admin_emails_for(self, game: str) -> set[str]:
    """Return the set of game-admin emails for the given game slug.

    Normalizes: 'hoard-hurt-help' → 'HOARD_HURT_HELP'.
    Falls back to admin_emails if no game-specific key and compat window active.
    """
    import logging
    key = game.upper().replace("-", "_")
    raw = self._game_admin_emails_raw.get(key, "")
    if not raw and self.admin_emails:
        logging.getLogger(__name__).warning(
            "ADMIN_EMAILS fallback active for game %s — set GAME_ADMIN_EMAILS__%s",
            game, key,
        )
        raw = self.admin_emails
    if not raw:
        return set()
    return {e.strip().lower() for e in raw.split(",") if e.strip()}
```

Note: `_game_admin_emails_raw` needs to use `model_config = SettingsConfigDict(... populate_by_name=True)` or be declared with `alias`. Alternatively, collect the raw dict as a class var computed once. Codex should verify pydantic-settings v2 field naming for private-style fields.

### 2. app/deps.py

Remove `require_admin`. Add:

```python
async def require_platform_admin(request: Request, db: DbSession) -> User:
    user = await require_user(request, db)
    if user.email.lower() not in settings.platform_admin_emails_set:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "NOT_PLATFORM_ADMIN", "message": "Platform admin access required.", "details": {}}},
        )
    return user


async def require_game_admin(
    game: str = Path(...),
    request: Request = None,
    db: DbSession = Depends(get_session),
) -> User:
    """Reads {game} from the URL path at runtime."""
    user = await require_user(request, db)
    if user.email.lower() not in settings.game_admin_emails_for(game):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": "NOT_GAME_ADMIN", "message": f"Game admin access required for {game}.", "details": {}}},
        )
    return user
```

Note: `require_game_admin` must be used via `Depends(require_game_admin)` (no call), so
FastAPI injects `game` from the path parameter. This ONLY works on routes that have a
`{game}` path parameter. All game admin web routes (`/games/{game}/admin/...`) and all
game admin API routes (`/api/game-admin/{game}/...`) have `{game}` in the path — this is
by design. Never use `require_game_admin` on a route without `{game}` in its path, or
FastAPI will raise a 422 at request time.

verification: `grep -rn "require_game_admin" app/routes/` after implementation must show
that every usage is on a route with `{game}` in its path string.

### 3. app/routes/web_support.py

```python
# Rename _is_admin → _is_any_admin
def _is_any_admin(user: User | None) -> bool:
    """True if user is platform admin or game admin for any game."""
    if user is None:
        return False
    email = user.email.lower()
    # settings is lru_cache'd; all_game_admin_emails_set is a property on the cached instance.
    return email in settings.platform_admin_emails_set or email in settings.all_game_admin_emails_set

# Add for viewer use:
def _is_game_admin(user: User | None, game: str) -> bool:
    if user is None:
        return False
    return user.email.lower() in settings.game_admin_emails_for(game)
```

The os.environ scan happens once at Settings construction (via `model_validator`), not per
request. Add to Settings:

```python
@property
def all_game_admin_emails_set(self) -> set[str]:
    """Union of all game admin emails across all configured games."""
    result: set[str] = set()
    for raw in self._game_admin_emails_raw.values():
        result.update(e.strip().lower() for e in raw.split(",") if e.strip())
    return result
```

`settings` is `@lru_cache`'d, so `_game_admin_emails_raw` is computed once at startup.

### 4. app/routes/admin_web.py

Keep only these route functions (platform-level):
- `admin_dashboard` (GET /admin)
- `admin_handles` (GET /admin/handles)
- `admin_reset_handle` (POST /admin/users/{user_id}/handle/reset)
- `admin_incidents` (GET /admin/incidents)
- `admin_incident_detail` (GET /admin/incidents/{id})

Remove: `create_game_form`, `create_game_submit`, `admin_game_detail`, `_render_add_sims`,
`add_sims_form`, `add_sims_submit`, `admin_start_game`, `admin_delete_game`, `admin_prompts`

Change all `require_admin` → `require_platform_admin`.

Update imports accordingly.

### 5. app/routes/game_admin_web.py (NEW)

Router prefix: `/games/{game}/admin`

Routes (all use `user: Annotated[User, Depends(require_game_admin)]`):
- GET `/` → `game_admin_dashboard` → `game_admin/dashboard.html`
- GET + POST `/matches/new` → `create_match_form` / `create_match_submit` → `game_admin/create_match.html`
- GET `/matches/{match_id}` → `game_admin_match_detail` → `game_admin/match_detail.html`
- GET + POST `/matches/{match_id}/bots` → `add_bots_form` / `add_bots_submit` → `game_admin/add_bots.html`
- POST `/matches/{match_id}/start` → `game_admin_start_match`
- POST `/matches/{match_id}/cancel` → `game_admin_cancel_match`
- GET `/prompts` → `game_admin_prompts` → `game_admin/prompts.html`

Each handler that loads a match verifies `match.game == game`, returning 404 if mismatched.

Logic is moved verbatim from admin_web.py handler bodies — no logic changes.

### 6. app/routes/game_admin_api.py (NEW)

Router prefix: `/api/game-admin/{game}`

Routes (all use `_: Annotated[User, Depends(require_game_admin)]`):
- POST `/matches` → create match (from admin_api.py `create_match`)
- POST `/matches/{match_id}/cancel` → cancel match (from admin_api.py `cancel_match`)
- GET `/matches/{match_id}/export.csv` → export CSV
- GET `/matches/{match_id}/export.json` → export JSON

Each handler verifies `match.game == game` (404 if mismatched).

Logic is moved verbatim from admin_api.py.

### 7. Template migration

Create `app/templates/game_admin/` directory.

Copy and rename:
- `admin/create_game.html` → `game_admin/create_match.html`  (update form action URLs)
- `admin/game_detail.html` → `game_admin/match_detail.html`  (update links)
- `admin/add_sims.html`   → `game_admin/add_bots.html`       (update form action URLs)
- `admin/prompts.html`    → `game_admin/prompts.html`        (no URL changes needed)

Create `game_admin/dashboard.html` — minimal page listing matches for this game,
with links to create match and view prompts.

Delete game-scoped templates from `admin/` after migration.
Update internal links from `/admin/matches/...` to `/games/{game}/admin/matches/...`.

### 8. web_viewer.py

Change:
```python
"is_admin": _is_admin(user),
```
to:
```python
"is_game_admin": _is_game_admin(user, match.game),
```

Update `from app.routes.web_support import _is_admin` to import `_is_game_admin`.
Update the viewer template to use `is_game_admin` (not `is_admin`) for strategy_text display.

### 9. Bulk rename in non-admin web routes

In each of these files, replace `_is_admin` import and call with `_is_any_admin`:
- `app/routes/web_lobby.py`
- `app/routes/web_player.py`
- `app/routes/web_analysis.py`
- `app/routes/handle_web.py`

Template `is_admin` variable name stays the same — templates don't need to change for
these six modules (nav display behavior is identical).

### 10. app/main.py

Add imports for both new routers and the web/auth/spectator routers (which are currently
only in conftest, not in create_app). Wire them in `create_app()`:

```python
from app.routes import (
    admin_web,
    auth,
    game_admin_api,
    game_admin_web,
    handle_web,
    nav_context,
    spectator_api,
    web,
    # existing imports...
)

# Inside create_app(), after existing include_router calls:
app.include_router(auth.router)
app.include_router(handle_web.router)
app.include_router(web.router, dependencies=[Depends(nav_context.populate_nav_cta)])
app.include_router(spectator_api.router)
app.include_router(admin_web.router)
app.include_router(game_admin_web.router)
app.include_router(game_admin_api.router)
```

### 11. tests/conftest.py

Remove the explicit `include_router` calls for the five routers now wired in `create_app()`:
- auth_router
- handle_web_router
- web_router
- spectator_api_router
- admin_web_router
- admin_api_router (file deleted)

Remove the corresponding imports. conftest.py still imports `app` from `app.main`, but
the `test_app.include_router(...)` lines are deleted — `create_app()` already wired them.

### 12. Tests

**tests/test_admin.py** — update:
- Replace any `require_admin` mock/override with `require_platform_admin`
- Add tests for the boundary criteria (AC 3–5, 10):
  ```
  test_game_admin_only_cannot_access_platform_admin()
  test_platform_admin_only_cannot_access_game_admin()
  test_game_admin_wrong_game_cannot_access()
  test_game_admin_api_routes_accessible()
  test_agent_api_not_shadowed_by_game_admin_api()
  ```

**tests/test_admin_add_sims.py** — update route URLs from `/admin/matches/{id}/sims`
to `/games/hoard-hurt-help/admin/matches/{id}/bots`; update admin override.

## Implementation Order

1. `app/config.py` — new settings (verify pydantic behavior with a unit test first)
2. `app/deps.py` — new deps, keep `require_admin` as deprecated alias during development
3. `app/routes/web_support.py` — rename `_is_admin` → `_is_any_admin`, add `_is_game_admin`
4. Bulk update non-admin routes (lobby, player, analysis, handle) — mechanical rename
5. `app/routes/admin_web.py` — strip game routes, swap dep
6. Create `app/routes/game_admin_web.py` — migrate game web routes
7. Create `app/routes/game_admin_api.py` — migrate game API routes
8. Create `app/templates/game_admin/` and migrate templates
   - Additionally: grep `app/templates/` and `app/static/` for hardcoded `/admin/`
     paths (`grep -rn '"/admin/' app/templates/ app/static/`). Update any found
     references in base.html, nav fragments, or JS to use the new paths.
9. Update `app/routes/web_viewer.py` — swap admin check
10. `app/main.py` — wire all routers
11. `tests/conftest.py` — remove duplicate includes
12. Remove `app/routes/admin_api.py`
13. Remove `require_admin` from `app/deps.py`
14. Tests — update + new boundary tests
15. Run preflight: `ruff check . && mypy app/ mcp_server/ && pytest -q`

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| pydantic-settings v2 `model_validator` + `os.environ` scan may conflict with test isolation | Unit test in step 1 verifies parsing before touching routes |
| Double-registration if conftest still mounts routers after main.py does | Step 11 removes conftest includes immediately after step 10 |
| Template URLs (form actions, redirects) in game_admin/ pointing to old /admin/ paths | Template migration step audits all `action=` and `href=` in the 4 migrated templates |
| `_is_any_admin` scanning os.environ on every request is slightly slower | Acceptable — settings is cached; this helper is a fallback nav check, not a hot path |

## Verification Checklist (from spec AC)

- [ ] require_platform_admin guards all /admin/ routes
- [ ] require_game_admin guards all /games/{game}/admin/ routes
- [ ] GAME_ADMIN_EMAILS__HOARD_HURT_HELP user → 403 on /admin/
- [ ] PLATFORM_ADMIN_EMAILS-only user → 403 on /games/hoard-hurt-help/admin/
- [ ] hoard-hurt-help admin → 403 on /games/other-game/admin/
- [ ] game admin can view strategy prompts via game admin routes
- [ ] platform admin cannot see strategy_text in /admin/ views
- [ ] all existing tests pass
- [ ] new boundary tests cover AC 3–5
- [ ] API boundary: /api/game-admin/hoard-hurt-help/matches accessible to game admin
- [ ] API boundary: /api/games/M_0001/poll still routes to agent API
- [ ] `grep -rn "require_admin\|_is_admin" app/` returns zero results
