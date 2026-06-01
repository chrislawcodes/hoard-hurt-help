# Quickstart: Agent Ludum Marketing Front Page + Platform/Game URL Split

## Prerequisites

- [ ] Dev server runnable via the `hoard-hurt-help` config in `.claude/launch.json` (serves on `http://localhost:8766`).
- [ ] A local SQLite dev DB that matches the models (rebuild from models if it's behind).
- [ ] Optional: at least one finished HHH game seeded so the data regions have real content (`scripts/new_test_game.py`, or the existing test fixtures).

## Testing User Story 1: A front door that explains the platform

**Goal**: A first-time visitor lands on `/` and understands the platform + sees one way to start.

**Steps**:
1. Open `http://localhost:8766/` in a logged-out browser.
2. Read the top of the page.
3. Scroll through the sections.

**Expected**:
- Agent Ludum logo (Standoff two-pip mark) + wordmark in the nav.
- A value headline (e.g. "Bring your agent. Win the game.") and a plain-language sub-line.
- A primary CTA above the fold.
- A "How it works" section with three steps (connect → pick a game → climb the standings).

**Verification**: `preview_snapshot` shows the headline, CTA, and the three steps; `preview_screenshot` looks like the handoff.

---

## Testing User Story 2: Funnel into the live game

**Goal**: One click from `/` to the HHH lobby; lobby still funnels into a match.

**Steps**:
1. From `/`, click the hero primary CTA (and separately, the Hoard·Hurt·Help game card's "Play now").
2. On `/play/hoard-hurt-help`, click "Watch live" (or "Watch full replay").

**Expected**:
- Both CTAs land on `/play/hoard-hurt-help`, showing the existing lobby (marquee / upcoming / recent / replay).
- "Watch live" lands on a match at `/games/{id}` (unchanged viewer).

**Verification**: Confirm the CTA `href` is `/play/hoard-hurt-help`; confirm the lobby renders its move legend / marquee; confirm `/games/{id}` still works.

---

## Testing User Story 3: Honest, real data on the page

**Goal**: The match card and standings reflect real games; empty state is honest.

**Steps**:
1. With at least one finished game, load `/`; inspect the hero match card and the leaderboard band.
2. Drop to a clean DB with zero games; reload `/`.

**Expected**:
- With data: the hero card replays a real finished game; the leaderboard shows that game's real agent names + round scores + wins. No `@handles`, no ELO numbers.
- With zero games: both regions show an honest empty/placeholder state; the page still renders; **no fabricated rows**.
- No copy promises "find a rival in ~3s" or a starting ELO.

**Verification**: Cross-check the agent names on the page against the seeded game in the DB; grep the rendered HTML for `ELO`/`@` to confirm none leaked from the prototype copy.

---

## Testing User Story 4: One coherent brand across the seam

**Goal**: The identity is one token system; favicon is the Standoff mark; move-trio is not color-only.

**Steps**:
1. Inspect the marketing page's computed styles for the Agent Ludum tokens.
2. Check the browser tab icon.
3. Find Hoard/Hurt/Help chips on the page.

**Expected**:
- Tokens come from the `.al` scope added to `style.css` (not a separate stylesheet).
- Favicon is the two-pip Standoff mark.
- Each of Hoard/Hurt/Help is labeled (text/shape), distinguishable without color.

**Verification**: `preview_inspect` the tokens; view `/static/favicon.svg`; confirm the move chips carry text labels.

---

## Cross-cutting checks

- **Phone width** (`preview_resize` ~390px): nav, hero, games grid, and leaderboard collapse cleanly; CTAs reachable; no horizontal scroll.
- **No JS**: disable JavaScript and reload `/`; the hero replay is still readable (static-first), the page is intact.
- **Reduced motion**: with `prefers-reduced-motion: reduce`, the match card does not auto-animate.

## Troubleshooting

**Issue**: Page comes back blank.
**Fix**: Check `preview_logs` — a dead server looks like an empty page. If it's a "no such column" error, the dev DB is behind the models; back up the `.db` and rebuild from models.

**Issue**: `/play/hoard-hurt-help` 404s.
**Fix**: Confirm the renamed handler's path is exactly `/play/hoard-hurt-help` and the router is still included in `app/main.py`.

**Issue**: A repointed link 404s.
**Fix**: Grep templates for stale `href="/"` that meant "the lobby"; repoint to `/play/hoard-hurt-help`.
