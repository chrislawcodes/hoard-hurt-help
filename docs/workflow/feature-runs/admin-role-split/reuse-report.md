# Reuse Report: admin-role-split

For each capability the feature needs, maps to: reuse / extend / justified-new.

## Auth: require_admin dependency

**Need:** A dependency that enforces admin access on routes.
**Existing:** `app/deps.py:require_admin` — checks `settings.admin_emails_set`.
**Decision:** `extend`
- `require_platform_admin` replaces `require_admin` for platform routes.
- `require_game_admin` is a new dep that reads `{game}` from the URL path.
- `require_admin` is deleted (no compat alias needed — internal only).

## Config: admin email list

**Need:** Per-role email sets (platform vs. per-game).
**Existing:** `app/config.py:admin_emails` + `admin_emails_set` property.
**Decision:** `extend`
- Add `platform_admin_emails` field + `platform_admin_emails_set` property.
- Add `_game_admin_emails_raw` dict via `model_validator` scanning `os.environ`.
- Add `game_admin_emails_for(game)` method.
- Keep `admin_emails` as a compat fallback field (warn on use; deprecated).

## Nav admin flag (_is_admin)

**Need:** A boolean for the nav template deciding whether to show the admin link.
**Existing:** `app/routes/web_support.py:_is_admin(user)` — checks single admin set.
**Decision:** `extend`
- Rename to `_is_any_admin(user)` — returns True if platform admin OR game admin for any configured game.
- Add `_is_game_admin(user, game)` for the viewer's strategy-prompt gate.
- All six callers (lobby, player, analysis, handle, viewer) updated to match.

## Platform admin web routes

**Need:** Routes for dashboard, handles, incidents.
**Existing:** These routes already exist in `app/routes/admin_web.py`.
**Decision:** `reuse` (strip + keep)
- Remove game-level routes from `admin_web.py`; keep the 5 platform-level route functions.
- Swap `require_admin` → `require_platform_admin` on each route.

## Platform admin templates

**Need:** dashboard.html, handles.html, incidents.html, incident_detail.html.
**Existing:** `app/templates/admin/` has all four.
**Decision:** `reuse` — unchanged, stay at their current paths.

## Game admin web routes

**Need:** Create match, match detail, add bots, start, cancel, delete, prompts.
**Existing:** In `admin_web.py` — to be extracted.
**Decision:** `justified-new` (new file: `app/routes/game_admin_web.py`)
- Logic is migrated from `admin_web.py` verbatim.
- New router prefix `/games/{game}/admin`; handler names change.
- New `require_game_admin` dep replaces `require_admin`.
- New cross-game isolation check (match.game == game → 404).

## Game admin API routes

**Need:** Create match, cancel, export CSV/JSON.
**Existing:** `app/routes/admin_api.py` has all four.
**Decision:** `justified-new` (new file: `app/routes/game_admin_api.py`)
- Logic migrated from `admin_api.py` verbatim.
- New prefix `/api/game-admin/{game}` avoids routing conflict with agent API.
- `admin_api.py` is deleted entirely.

## Game admin templates

**Need:** create_match.html, match_detail.html, add_bots.html, prompts.html.
**Existing:** `admin/create_game.html`, `admin/game_detail.html`, `admin/add_sims.html`, `admin/prompts.html`.
**Decision:** `extend` (copy → rename → update internal URLs)
- Copied to `templates/game_admin/` with new names.
- Internal form action URLs updated from `/admin/matches/...` to `/games/{game}/admin/matches/...`.
- Source files deleted from `templates/admin/`.

## Router wiring in main.py

**Need:** Platform-admin and game-admin routers registered in `create_app()`.
**Existing:** `app/main.py:create_app` registers agent-API + connections only. Admin routers are in `tests/conftest.py`.
**Decision:** `extend`
- Move all router registrations into `create_app()` (platform-admin, game-admin, web, auth, spectator, handle).
- `tests/conftest.py` removes the now-duplicate `include_router` calls.

## Test fixtures for admin auth

**Need:** Override admin deps in tests.
**Existing:** `tests/test_admin.py` overrides `require_admin`.
**Decision:** `extend`
- Update override to `require_platform_admin` or `require_game_admin` as appropriate.
- New boundary tests added to existing file.
