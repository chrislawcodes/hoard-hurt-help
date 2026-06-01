# Plan Summary: Agent Ludum Marketing Front Page + Platform/Game URL Split

## Files In Scope

| File | Change | Notes |
|------|--------|-------|
| `app/routes/web.py` | modify | Rename `home()` to serve `GET /play/hoard-hurt-help`; add new `GET /` marketing handler that reuses `_featured_replay` + `_top_standings`. |
| `app/routes/lobby_views.py` | create (optional) | Only if extraction reads cleaner: behavior-preserving move of shared public-page helpers (`_player_count`, `_top_standings`, `_featured_replay` + deps). Default: skip, keep in `web.py`. |
| `app/templates/agent_ludum.html` | create | Marketing page: nav, hero + live match card, how-it-works (3 steps), games grid, leaderboard band, CTA band, footer. |
| `app/templates/home.html` | keep | Now rendered at `/play/hoard-hurt-help`; content unchanged. |
| `app/templates/base.html` | modify (light) | Marketing nav/shell; repoint site-title; keep game-page header/footer. |
| `app/static/style.css` | modify | Add Agent Ludum `.al` / `.al-plum` token scope + fonts + marketing component styles. Extend existing vars, no parallel sheet. |
| `app/static/favicon.svg` | replace | Standoff two-pip mark. |
| `app/templates/my_games.html` | modify | "Browse the lobby →" link → `/play/hoard-hurt-help`. |
| `app/templates/bots/_status.html` | modify | "Find a game to join →" → `/play/hoard-hurt-help`. |
| `app/templates/bots/detail.html` | modify | Two lobby links → `/play/hoard-hurt-help`. |
| `app/templates/join.html` | modify | "Cancel" → `/play/hoard-hurt-help`; "← Home" may stay `/`. |
| `tests/test_*.py` | create | Route-move test + honest empty-data test + link-sweep. |

## Migration Steps

None. No schema changes, no Alembic migration, no new dependencies.

## Data Model

None. Read-only use of existing `Game` / `Player` data via existing helpers.

## Key Constraints

- **Route move, not redirect**: `GET /` renders marketing; lobby moves to `GET /play/hoard-hurt-help`. — *Why: the spec needs `/` to BE the marketing page (US1), and a redirect would leave no front door.*
- **Do not use `/games/hoard-hurt-help`**: lobby goes under `/play/...`. — *Why: `/games/{game_id}` is the per-match viewer pattern; a game-type slug there would collide with match IDs.*
- **Reuse `_featured_replay` + `_top_standings`**: marketing data regions call the lobby's existing helpers. — *Why: guarantees real data (US3) with no drift and no new query logic.*
- **Real data only**: no fabricated ELO, no `@owner` handles, no instant-matchmaking copy. — *Why: clicking through to reality must not contradict the page; trust over flash (FR-009/FR-010).*
- **Teaser games disabled**: Tell / Holdfast / Accord are clearly not-yet-playable cards. — *Why: they're fictional; presenting them as live would be dishonest (FR-007).*
- **Extend the token system, don't fork it**: identity lives as an `.al` scope in `style.css`. — *Why: FR-012, and a scoped block supports the two-surface (Lilac + Plum) design without fighting the `data-theme` switcher.*
- **Static-first + reduced-motion**: hero match card correct on first paint with no JS; reuse home.html's existing auto-play pattern. — *Why: FR-015; accessibility and the no-SPA constraint.*
- **No internal link 404s**: repoint every "go to the lobby" link. — *Why: FR-004 / SC-004.*
