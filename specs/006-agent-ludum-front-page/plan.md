# Implementation Plan: Agent Ludum Marketing Front Page + Platform/Game URL Split

**Branch**: `feat/agent-ludum-front-page` | **Date**: 2026-05-31 | **Spec**: [spec.md](spec.md)

## Summary

Move the existing Hoard·Hurt·Help lobby from `GET /` to `GET /play/hoard-hurt-help`, and serve a new Agent Ludum marketing page at `GET /`. The marketing page is a server-rendered Jinja template that reuses the lobby's existing data helpers (`_featured_replay`, `_top_standings`) for its two real-data regions, recreates the Claude Design handoff's look in `app/static/style.css` by extending the existing CSS-variable token system, and repoints internal "go to the lobby" links. No database, no new dependencies, no SPA.

## Technical Context

**Language/Version**: Python 3 (async FastAPI app)
**Primary Dependencies**: FastAPI, Starlette templating (Jinja2), HTMX (already vendored in `app/static/`), SQLAlchemy (read-only here). No new dependencies.
**Storage**: N/A — read-only use of existing `Game` / `Player` data; no migrations.
**Testing**: pytest (in-memory SQLite test DB), ruff, mypy — per CLAUDE.md Preflight Gate.
**Target Platform**: Single Railway instance, server-rendered HTML; must work on mobile and with JS disabled.
**Performance Goals**: Marketing `/` is essentially static plus two cheap reads (one featured replay, one standings list) — same cost the lobby already pays today. No new SSE stream on `/`.
**Constraints**: No client-side SPA; live updates are SSE-swapped HTML fragments; static-first (correct on first paint without JS); `prefers-reduced-motion` respected. From spec: real data only (no fabricated ELO/handles/matchmaking copy); teaser games disabled.
**Scale/Scope**: One new route handler, one route rename, one new template (+ small partials), CSS token additions, one favicon swap, link repoints. Three humans use the site; this targets the first-time visitor.

## Constitution Check

**Status**: PASS

Validated against `CLAUDE.md` and `DESIGN.md`:

### Code standards (CLAUDE.md)
- [x] New route handlers are `async def` (Async Consistency).
- [x] All new function signatures typed; no `# type: ignore` / `# noqa` (No Suppressions, Type Annotations).
- [x] Specific exceptions only; no bare `except` (existing helpers already comply).
- [x] No vague filenames — new template is `agent_ludum.html`; any extracted helper module gets a domain name (`lobby_views.py`), not `utils.py` (File Structure).
- [x] App code stays in `app/`; no mixing with `mcp_server/` (File Structure).

### Testing (CLAUDE.md)
- [x] New tests use the in-memory SQLite test DB; external APIs not involved here.
- [x] Tests cover the routing move and the honest empty-data state (Testing Requirements).
- [x] Engine tests (`app/engine/*`) untouched — this is a web/route change only.

### Architecture (DESIGN.md §11 — platform + game modules)
- [x] `/` is the platform face; `/play/hoard-hurt-help` is game #1's lobby — consistent with the registry model.
- [x] No game-specific rules leak into the platform shell; the marketing page reads game-agnostic lobby views, and game identity ("hoard-hurt-help") is a path constant, not new coupling.

**Violations/Notes**: None. The one judgment call — whether to extract the lobby's private helpers into a shared module — is resolved below (Decision 3) in favor of a behavior-preserving extraction only if reuse across two handlers requires it.

## Architecture Decisions

### Decision 1: Route move — rename, don't redirect

**Chosen**: Rename the existing lobby handler to serve `GET /play/hoard-hurt-help`, and add a brand-new `GET /` handler for the marketing page. Do **not** keep `/` serving the lobby behind a redirect.

**Rationale**:
- The spec wants `/` to *be* the marketing page (US1), so `/` must render new content, not redirect.
- Keeping the lobby logic intact under a new path is the smallest behavior-preserving move (FR-002): same context, same template, same states.
- Internal links are repointed (FR-004) so nothing depends on `/` meaning "lobby" anymore.

**Alternatives Considered**:
- *301 redirect `/` → `/play/hoard-hurt-help`*: contradicts US1 (there'd be no marketing page).
- *Keep `/` as lobby, add `/agent-ludum`*: puts the marketing page in a second-class URL; the platform's front door should be `/`.

**Tradeoffs**: Pro — clean separation, no redirect hops. Con — old external bookmarks to `/` now see marketing (acceptable: lobby is one click away, per spec Edge Cases).

### Decision 2: Reuse lobby data helpers for the marketing data regions

**Chosen**: The marketing `/` handler computes its hero match card from the existing `_featured_replay(...)` and its leaderboard band from the existing `_top_standings(...)` — the same functions the lobby uses today ([app/routes/web.py:62](../../app/routes/web.py), [:137](../../app/routes/web.py)).

**Rationale**:
- Guarantees the marketing page shows *real* data (US3 / FR-008 / FR-009) with zero new query logic.
- One source of truth for "what's the featured replay / who's leading" — no drift between `/` and `/play/hoard-hurt-help`.

**Alternatives Considered**:
- *New bespoke queries for the marketing page*: duplicates logic, risks drift, more to test. Rejected.

**Tradeoffs**: Pro — honest data for free, less code. Con — couples the marketing handler to lobby helpers; mitigated by Decision 3.

### Decision 3: Share the helpers via a behavior-preserving extraction (only as needed)

**Chosen**: If both handlers end up in the same module (`web.py`), call the helpers directly (they're module-level already). If the marketing handler moves to its own module for clarity, extract the shared read helpers (`_player_count`, `_top_standings`, `_featured_replay`, and the small `_move_effect_for` / `_final_round_moments` they depend on) into a domain-named module `app/routes/lobby_views.py` and import them from both — **no behavior change**, pure move.

**Rationale**:
- CLAUDE.md forbids vague filenames and rewards focused files; a `lobby_views.py` that holds "how we summarize games for public pages" is a meaningful unit shared by the lobby and the marketing page.
- Keeping the extraction behavior-preserving keeps the existing lobby tests green as the guard.

**Tradeoffs**: Pro — clean reuse, focused files. Con — a move diff touches more lines; mitigated by tests. *Default to the simplest option that compiles cleanly: keep both handlers in `web.py` and skip the extraction unless it reads better.*

### Decision 4: Fold the Agent Ludum identity into the existing token system

**Chosen**: Add the Lilac/Plum identity to `app/static/style.css` as an **Agent Ludum surface scope** rather than a 15th switchable theme. Concretely: a scoped block (e.g. `.al` / `.al-plum` on the marketing page wrapper) that defines the handoff tokens (`--bg`, `--surface`, `--ink`, `--brand` orange, `--brand-2` violet, the fixed `--hoard/--hurt/--help` trio, radii, shadows), plus the three Google fonts via the existing `@import`/font strategy. The marketing template opts into this scope; the rest of the site keeps its current themes untouched.

**Rationale**:
- Extends the existing variable system instead of creating a parallel stylesheet (FR-012).
- Scoping to the marketing page avoids disturbing the game viewer's 14 themes (explicitly out of scope) and avoids fighting the `data-theme` switcher in `base.html`.
- The Lilac (light) / Plum (dark "arena") two-surface split maps cleanly onto nested scopes — the hero match card and leaderboard band get `.al-plum`.

**Alternatives Considered**:
- *Add "Agent Ludum" as a `data-theme`*: the identity is a fixed brand, not a user-selectable theme; and the design needs *two* surfaces on one page (Lilac + Plum), which a single `data-theme` can't express.
- *Separate `agent_ludum.css`*: violates "extend, don't create a parallel system" (FR-012).

**Tradeoffs**: Pro — one token system, no theme-switcher conflict, two-surface support. Con — a scoped token block is slightly more CSS; acceptable.

### Decision 5: Hero match card = the existing static-first replay pattern, not React

**Chosen**: Render the hero match card from the real featured replay using the existing `fragments/featured_replay.html` markup pattern and the existing static-first auto-play script already in `home.html` ([:108](../../app/templates/home.html)). The prototype's `match-sim.jsx` (typewriter, scripted payoff matrix) is **reference for visual treatment only** and is not ported.

**Rationale**:
- FR-008/FR-015: real data, correct on first paint without JS, reduced-motion respected — the existing pattern already does all three.
- No React/Babel in production (handoff README says the same).

**Tradeoffs**: Pro — reuses a shipped, accessible pattern; real data. Con — less flashy than the typewriter sim; acceptable and more honest.

### Decision 6: Favicon swap

**Chosen**: Replace `app/static/favicon.svg` with the Standoff two-pip mark (orange rounded tile, two pips, faint divider) per the handoff geometry. Single file swap; `base.html` already references `/static/favicon.svg`.

**Rationale**: FR-013; one-line asset change, no template edit needed.

## Project Structure

### Monolithic FastAPI app (`app/`)

```
app/
├── routes/
│   └── web.py            - MODIFY: rename home() → serve GET /play/hoard-hurt-help;
│                           add new GET / marketing handler. (Optional) extract shared
│                           read helpers to lobby_views.py — Decision 3.
│   └── lobby_views.py    - CREATE (only if extraction chosen): shared public-page game
│                           summary helpers (_player_count, _top_standings,
│                           _featured_replay + deps), behavior-preserving move.
├── templates/
│   ├── agent_ludum.html  - CREATE: the marketing page (nav, hero+match card, how-it-works,
│                           games grid, leaderboard band, CTA band, footer).
│   ├── home.html         - KEEP: now rendered at /play/hoard-hurt-help (unchanged content).
│   ├── base.html         - MODIFY (light): the marketing page may need a minimal/no-chrome
│                           shell or its own nav; keep the game pages' header/footer as-is.
│   └── fragments/        - REUSE: featured_replay.html, move_legend.html as needed.
└── static/
    ├── style.css         - MODIFY: add the Agent Ludum (.al / .al-plum) token scope,
    │                       fonts, and the marketing-page component styles.
    └── favicon.svg       - REPLACE: Standoff two-pip mark.

Internal link repoints (MODIFY): templates whose intent is "go to the lobby":
  - my_games.html ("Browse the lobby →"), bots/_status.html & bots/detail.html
    ("Find a game to join →"/"Browse the lobby →"), join.html "Cancel" → /play/hoard-hurt-help.
  - base.html site-title and "← Home" links → / (now the marketing home) or the AL nav.
  - auth.py logout redirect stays "/" (now marketing home) — fine.

tests/
└── test_*                - CREATE: route-move test (GET / serves marketing; GET
                            /play/hoard-hurt-help serves the lobby), and honest
                            empty-data test (no games → no fabricated rows, page still renders).
```

**Structure Decision**: This feature touches only the web layer (`app/routes/web.py`, templates, `style.css`, favicon) and tests. No models, no migrations, no engine, no MCP server. The default path keeps both handlers in `web.py`; `lobby_views.py` is created only if the shared import reads cleaner than leaving the helpers in place.

## Testing Strategy

- **Route move** (FR-001/FR-002/FR-003): `GET /` returns the marketing page (assert on an Agent Ludum marker + the primary CTA href `/play/hoard-hurt-help`); `GET /play/hoard-hurt-help` returns the lobby (assert on a lobby marker, e.g. the move legend / marquee). `GET /games/{id}` still works.
- **Honest empty state** (FR-011): with zero games, `GET /` renders 200, the hero/leaderboard show the empty/placeholder copy, and no fabricated agent rows appear.
- **Real-data wiring** (FR-008/FR-009): with one finished game seeded (existing test helpers), the leaderboard band shows that game's real agent name(s); the hero references the real replay.
- **No-404 link sweep** (FR-004/SC-004): a test (or grep-backed assertion) that the repointed links resolve.
- Existing lobby tests act as the guard that the move preserved behavior.

See [quickstart.md](quickstart.md) for the manual walk-through.
