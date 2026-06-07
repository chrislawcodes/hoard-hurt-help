# Spec: Split Admin into Platform Admin and Game Admin

**Slug:** admin-role-split
**Branch:** claude/awesome-bohr-fBDnG

## Background

The platform has one admin concept: an email in ADMIN_EMAILS gets access to
everything via require_admin. That single role mixes two distinct jobs:
- Platform-level: game catalog, admin allowlist, platform health (incidents,
  handle resets). Chris-as-operator.
- Game-level: creating/cancelling matches, match detail, strategy prompts,
  export. Chris-as-experimenter, potentially a different person per title.

The design decisions are made (AGENT_LUDUM_DESIGN.md §6,
HOARD_HURT_HELP_DESIGN.md §5). This feature implements them.

## Changes

### 1. Config (app/config.py)

#### Canonical slug mapping
The game slug is `hoard-hurt-help` (as stored in the database `game` column and
used in URLs). The env var suffix uses the uppercase-with-underscores form:
`HOARD_HURT_HELP`. The `game_admin_emails_for(game)` method normalizes any
incoming slug by uppercasing and replacing hyphens with underscores before
looking up the env key. All specs, tests, and acceptance criteria use the
canonical slug `hoard-hurt-help`; `HHH` is never used.

#### Env var changes
Old: ADMIN_EMAILS=...
New:
  PLATFORM_ADMIN_EMAILS=chris@example.com
  GAME_ADMIN_EMAILS__HOARD_HURT_HELP=chris@example.com

#### Compatibility window (avoids lockout during deploy)
During rollout, old env var ADMIN_EMAILS may still be set. Settings reads it
as a fallback for both platform admin and game admin if the new vars are empty:
  - If PLATFORM_ADMIN_EMAILS is unset, fall back to ADMIN_EMAILS
  - If GAME_ADMIN_EMAILS__HOARD_HURT_HELP is unset, fall back to ADMIN_EMAILS
  - Log a deprecation warning when the fallback fires
  - ADMIN_EMAILS is removed from config once prod env vars are confirmed updated
This means a deploy with only old env vars still works (no lockout), and a
deploy with only new env vars also works. Mixed state during a rolling deploy
is safe.

Settings class gains:
  platform_admin_emails: str  →  platform_admin_emails_set: set[str] property
  game_admin_emails: dict[str, str] via env prefix GAME_ADMIN_EMAILS__
    Each value is a comma-separated list of email addresses, e.g.:
    GAME_ADMIN_EMAILS__HOARD_HURT_HELP=alice@example.com,bob@example.com
  game_admin_emails_for(game: str) -> set[str] method:
    1. Normalizes game slug: upper().replace("-", "_")
    2. Looks up the key in game_admin_emails dict
    3. Splits the value on "," and strips whitespace
    4. Falls back to admin_emails (ADMIN_EMAILS) if key not found and ADMIN_EMAILS set

RISK: pydantic-settings nested dict support (__ delimiter) may need a custom
validator. Verify against installed version.
verification: unit test sets GAME_ADMIN_EMAILS__HOARD_HURT_HELP=a@b.com and
asserts settings.game_admin_emails_for("hoard-hurt-help") == {"a@b.com"}

### 2. Auth deps (app/deps.py)
Remove require_admin. Add:
  async def require_platform_admin(request, db) -> User
    raises HTTP 403 code=NOT_PLATFORM_ADMIN

  async def require_game_admin(
      game: str = Path(...),          # reads the {game} path parameter at runtime
      request: Request = None,
      db: AsyncSession = Depends(get_db),
  ) -> User:
      # checks settings.game_admin_emails_for(game)
      # raises HTTP 403 code=NOT_GAME_ADMIN

Usage: user: Annotated[User, Depends(require_game_admin)]
The dependency reads `game` from the URL path at call time — it is NOT a
compile-time factory with a hardcoded string. This is the FastAPI pattern for
path-parameter-aware dependencies.

Note: For routes that do NOT have a {game} path param but still need per-game
auth (e.g. a direct match endpoint), the game is looked up from match.game in
the handler before calling a separate verify_game_admin() helper.

Remove _is_admin() from web_support.py; routes pass is_platform_admin /
is_game_admin flags to templates explicitly.

### 3. Platform admin routes (admin_web.py / admin_api.py)
Stripped to platform-only. Keeps:
  GET /admin  (dashboard — platform health summary, not match list)
  GET /admin/handles
  POST /admin/users/{user_id}/handle/reset
  GET /admin/incidents
  GET /admin/incidents/{id}

Removes: all match routes, /admin/prompts.
admin_api.py likely becomes empty after split (plan decides keep vs remove).
All require_admin → require_platform_admin.

### 4. New game admin routes
app/routes/game_admin_web.py  prefix=/games/{game}/admin
app/routes/game_admin_api.py  prefix=TBD (see routing conflict below)

All handlers use Depends(require_game_admin(game)). Each match-loading handler
verifies match.game == game (return 404 if mismatched — cross-game isolation).

Web routes:
  GET  /games/{game}/admin/
  GET/POST /games/{game}/admin/matches/new
  GET  /games/{game}/admin/matches/{match_id}  (shows strategy prompts)
  GET/POST /games/{game}/admin/matches/{match_id}/bots
  POST /games/{game}/admin/matches/{match_id}/start
  POST /games/{game}/admin/matches/{match_id}/cancel
  GET  /games/{game}/admin/prompts

API routes (prefix /api/game-admin/{game}):
  POST   /api/game-admin/{game}/matches
  POST   /api/game-admin/{game}/matches/{match_id}/cancel
  GET    /api/game-admin/{game}/matches/{match_id}/export.csv
  GET    /api/game-admin/{game}/matches/{match_id}/export.json

ROUTING: The agent API is mounted at /api/games/{match_id}. The game-admin API
uses /api/game-admin/{game}/ — a distinct, non-overlapping prefix with no
shared path segment. No path guards, no registration-order dependency.
app/routes/game_admin_api.py uses APIRouter(prefix="/api/game-admin/{game}").
verification: integration test hits /api/game-admin/hoard-hurt-help/matches
(→ game admin handler) and /api/games/M_0001/poll (→ agent API handler);
both must return the correct handler regardless of router registration order.

### 5. Templates
Move game-scoped templates to templates/game_admin/:
  admin/create_game.html   → game_admin/create_match.html
  admin/game_detail.html   → game_admin/match_detail.html
  admin/add_sims.html      → game_admin/add_bots.html
  admin/prompts.html       → game_admin/prompts.html

Platform admin templates stay in templates/admin/; dashboard updated.

### 6. Wire routes into app/main.py
Register both admin routers in create_app(). Update tests/conftest.py to
remove the duplicate include_router calls (or rebuild test app from
create_app() directly).
RISK: double-registration if conftest still mounts them after main.py does.
verification: pytest -q clean run; no AssertionError: Ambiguous route.

### 7. Strategy prompt visibility
SpectatorState schema never includes strategy_text — that structural exclusion
stays. Game admin views prompts only through game admin routes.
Check web_viewer.py for any _is_admin() check that gates strategy prompt
rendering — if present, replace with is_game_admin_for_this_game.
verification: render viewer HTML as platform-admin-only user; confirm no
strategy_text appears.

## Acceptance Criteria
1. require_platform_admin guards all /admin/ routes
2. require_game_admin guards all /games/{game}/admin/ routes (reads game from path)
3. User with only GAME_ADMIN_EMAILS__HOARD_HURT_HELP set → 403 on /admin/
4. User with only PLATFORM_ADMIN_EMAILS set → 403 on /games/hoard-hurt-help/admin/
5. hoard-hurt-help game admin → 403 on /games/other-game/admin/
6. hoard-hurt-help game admin can view strategy prompts via game admin routes
7. Platform admin cannot reach strategy prompts via any /admin/ route
8. All existing tests pass
9. New boundary tests cover criteria 3–5
10. API boundary tests: game admin can hit /api/game-admin/hoard-hurt-help/matches;
    non-game-admin cannot; agent API /api/games/M_0001/poll still works
11. grep for require_admin and _is_admin in the entire codebase confirms zero
    remaining usages after migration

## Non-Goals
- No change to match logic, turn loop, scheduling, export logic, bot seeding
- No change to GameModule contract
- No change to public spectator UX or player-facing pages
