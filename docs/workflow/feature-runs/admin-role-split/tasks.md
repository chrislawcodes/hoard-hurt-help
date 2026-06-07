# Tasks: Admin Role Split

**Prerequisites**: plan.md, spec.md
**Branch**: claude/awesome-bohr-fBDnG

## Format: `[ID] [P: file]? [Story]? Description`

- **[P: repo/relative/file.ext]**: Can run in parallel — file list must be present.
- **[USN]**: User story label.
- **[CHECKPOINT]**: Git commit boundary. Run preflight before marking complete.

---

## Phase 1: Foundation — Config + Auth Deps

**Purpose**: New settings fields and auth dependencies. `require_admin` stays for now
(removed in slice 5). Preflight must pass before proceeding.

**Est diff**: ~120 lines changed

- [X] T001 [P: app/config.py] Add `platform_admin_emails: str` field, `platform_admin_emails_set` property with `ADMIN_EMAILS` fallback + deprecation warning. Add `@model_validator(mode='before')` that scans `os.environ` for `GAME_ADMIN_EMAILS__*` keys into `_game_admin_emails_raw: dict`. Add `game_admin_emails_for(game)` method (normalizes slug, comma-splits, falls back to `admin_emails`). Add `all_game_admin_emails_set` property (union across all game keys). Unit test in `tests/test_config_admin.py`: set env var `GAME_ADMIN_EMAILS__HOARD_HURT_HELP=a@b.com` and assert `settings.game_admin_emails_for("hoard-hurt-help") == {"a@b.com"}`.

- [X] T002 [P: app/deps.py] Add `require_platform_admin` and `require_game_admin` (path-param dep reading `game: str = Path(...)`). Keep `require_admin` unchanged for now. Import `Path` from fastapi if not already imported.

**[CHECKPOINT]**: Foundation — `pytest -q tests/test_config_admin.py` passes; `mypy app/` clean.

---

## Phase 2: US1 — Platform Admin Separation

**Goal**: Platform routes protected by `require_platform_admin`; `_is_admin()` replaced.

**Independent Test**: Hit `/admin/` as platform-admin-only user → 200. Hit same URL as
game-admin-only user → 403.

**Est diff**: ~180 lines changed

- [X] T003 [P: app/routes/web_support.py] Rename `_is_admin(user)` → `_is_any_admin(user)` (checks `platform_admin_emails_set OR all_game_admin_emails_set`). Add `_is_game_admin(user, game)`. Keep same function signature shape.

- [X] T004 [P: app/routes/web_lobby.py] Replace `from app.routes.web_support import _is_admin` with `_is_any_admin`; rename all `_is_admin(` calls to `_is_any_admin(`. Template variable name `is_admin` stays the same.

- [X] T005 [P: app/routes/web_player.py] Same `_is_admin` → `_is_any_admin` rename.

- [X] T006 [P: app/routes/web_analysis.py] Same `_is_admin` → `_is_any_admin` rename.

- [X] T007 [P: app/routes/handle_web.py] Same `_is_admin` → `_is_any_admin` rename.

- [X] T008 [US1] Strip `admin_web.py` to platform-only routes. Remove route functions: `create_game_form`, `create_game_submit`, `admin_game_detail`, `_render_add_sims`, `add_sims_form`, `add_sims_submit`, `admin_start_game`, `admin_delete_game`, `admin_prompts`. Change all remaining `require_admin` → `require_platform_admin`. Update import: `from app.deps import DbSession, require_platform_admin`. Remove unused imports.

**[CHECKPOINT]**: Platform admin — `pytest -q` passes; `ruff check .` clean; `mypy app/` clean.

---

## Phase 3: US2 — Game Admin Routes

**Goal**: New game admin routes and templates under `/games/{game}/admin/` and
`/api/game-admin/{game}/`.

**Independent Test**: Hit `/games/hoard-hurt-help/admin/` as game admin → 200.
Hit same URL as platform-admin-only user → 403.

**Est diff**: ~370 lines changed (new files, template copies)

- [X] T009 [US2] Create `app/templates/game_admin/` directory. Copy and rename templates:
  `admin/create_game.html` → `game_admin/create_match.html`;
  `admin/game_detail.html` → `game_admin/match_detail.html`;
  `admin/add_sims.html` → `game_admin/add_bots.html`;
  `admin/prompts.html` → `game_admin/prompts.html`.
  Create `game_admin/dashboard.html` — minimal page listing matches for this game with links to create match and view prompts.
  Update internal URLs in migrated templates: all `/admin/matches/...` → `/games/{{ game }}/admin/matches/...`.
  Run `grep -rn '"/admin/' app/templates/ app/static/` — fix any found hardcoded `/admin/` paths in `base.html` or JS.

- [X] T010 [US2] Create `app/routes/game_admin_web.py`. Router prefix `/games/{game}/admin`. Routes:
  `GET /` → `game_admin_dashboard` → `game_admin/dashboard.html`;
  `GET /matches/new` + `POST /matches/new` → create match → `game_admin/create_match.html`;
  `GET /matches/{match_id}` → match detail → `game_admin/match_detail.html`;
  `GET /matches/{match_id}/bots` + `POST /matches/{match_id}/bots` → add bots → `game_admin/add_bots.html`;
  `POST /matches/{match_id}/start` → start match;
  `POST /matches/{match_id}/cancel` → cancel match;
  `GET /prompts` → prompts page → `game_admin/prompts.html`.
  All handlers: `user: Annotated[User, Depends(require_game_admin)]`. Each match-loading handler verifies `match.game == game` → 404 if mismatched. Logic migrated verbatim from `admin_web.py` route bodies.

- [X] T011 [P: app/routes/game_admin_api.py] Create `app/routes/game_admin_api.py`. Router prefix `/api/game-admin/{game}`. Routes:
  `POST /matches` → create match;
  `POST /matches/{match_id}/cancel` → cancel;
  `GET /matches/{match_id}/export.csv` → CSV export;
  `GET /matches/{match_id}/export.json` → JSON export.
  All handlers: `_: Annotated[User, Depends(require_game_admin)]`. Each verifies `match.game == game` → 404. Logic migrated verbatim from `admin_api.py`.

**[CHECKPOINT]**: Game admin routes — `pytest -q` passes; new routes return correct status codes.

---

## Phase 4: US3 — Integration, Wiring, Cleanup

**Goal**: All routers wired in `create_app()`; no remaining `require_admin` or `_is_admin`;
viewer uses `is_game_admin` for strategy prompt gating; boundary tests pass.

**Independent Test**: All spec AC 1–11 pass. `grep -rn "require_admin\|_is_admin" app/` → zero results.

**Est diff**: ~160 lines changed

- [X] T012 [US3] Update `app/main.py`: import and register inside `create_app()`:
  `admin_web.router`, `game_admin_web.router`, `game_admin_api.router`,
  `auth.router`, `handle_web.router`, `web.router` (with `nav_context.populate_nav_cta`), `spectator_api.router`.

- [X] T013 [US3] Update `tests/conftest.py`: remove explicit `include_router` calls for routers now in `create_app()`. Remove corresponding imports. Verify no double-registration: `pytest -q` must not print `AssertionError: Ambiguous route`.

- [X] T014 [US3] Delete `app/routes/admin_api.py` (all routes moved to `game_admin_api.py`).

- [X] T015 [US3] Remove `require_admin` from `app/deps.py`. Verify: `grep -rn "require_admin" app/` → zero results.

- [X] T016 [P: app/routes/web_viewer.py] [US3] Replace `_is_admin(user)` with `_is_game_admin(user, match.game)`. Update import. Change template context key from `"is_admin"` to `"is_game_admin"`. Update `app/templates/` viewer template: replace `is_admin` with `is_game_admin` for strategy_text gating only.

- [X] T017 [P: app/templates/admin/] [US3] Delete game-scoped templates from `app/templates/admin/`: `create_game.html`, `game_detail.html`, `add_sims.html`, `prompts.html`.

- [X] T018 [US3] Add boundary tests in `tests/test_admin.py`:
  `test_game_admin_only_cannot_access_platform_admin` — 403 on GET /admin/;
  `test_platform_admin_only_cannot_access_game_admin` — 403 on GET /games/hoard-hurt-help/admin/;
  `test_game_admin_wrong_game_cannot_access` — 403 on GET /games/other-game/admin/;
  `test_game_admin_api_accessible` — 200/201 on POST /api/game-admin/hoard-hurt-help/matches;
  `test_agent_api_not_shadowed` — agent poll route still reachable.
  Update `tests/test_admin_add_sims.py`: route URL `/admin/matches/{id}/sims` → `/games/hoard-hurt-help/admin/matches/{id}/bots`.

- [X] T019 [US3] Final verification: `grep -rn "require_admin\|_is_admin" app/` → zero results. Run full preflight: `ruff check . && mypy app/ mcp_server/ && pytest -q`.

**[CHECKPOINT]**: All boundary tests pass; grep returns zero; preflight green.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Foundation)**: No dependencies. Produces `require_platform_admin` / `require_game_admin`.
- **Phase 2 (US1)**: Depends on Phase 1. Uses `require_platform_admin`.
- **Phase 3 (US2)**: Depends on Phase 1. Can start in parallel with Phase 2.
- **Phase 4 (US3)**: Depends on Phases 2 + 3.

### Parallel Opportunities Within Phases

- **Phase 1**: T001 + T002 are different files.
- **Phase 2**: T003–T007 are all different files.
- **Phase 3**: T010 (web routes) + T011 (API routes) are different files.
- **Phase 4**: T016 + T017 are different files/dirs.

### Checkpoint Summary

| Checkpoint | Gate |
|-----------|------|
| Phase 1 | `pytest tests/test_config_admin.py -q`; `mypy app/` clean |
| Phase 2 | `pytest -q`; `ruff`; `mypy` clean |
| Phase 3 | `pytest -q`; new route tests pass |
| Phase 4 | `grep` zero; full preflight green |
