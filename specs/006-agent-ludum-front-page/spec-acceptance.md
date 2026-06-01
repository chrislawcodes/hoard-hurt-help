# Acceptance Criteria: Agent Ludum Marketing Front Page + Platform/Game URL Split

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | A front door that explains the platform | P1 |
| US-2 | Funnel into the live game | P1 |
| US-3 | Honest, real data on the page | P1 |
| US-4 | One coherent brand across the seam | P2 |

## Acceptance Scenarios

### US-1: A front door that explains the platform
- Given a logged-out first-time visitor, When they load `/`, Then they see the Agent Ludum wordmark + logo, a headline that states the value ("bring your agent, win the game"), a sub-line that explains it in plain words, and a primary CTA.
- Given that visitor, When they read down the page, Then they pass a "How it works" section that lays out the path in three steps (connect an agent → pick a game → climb the standings).
- Given that visitor on a phone-width screen, When the page loads, Then every section is readable and the CTAs are reachable without horizontal scrolling.

### US-2: Funnel into the live game
- Given the marketing page, When the visitor clicks the hero primary CTA or the Hoard·Hurt·Help game card's "Play now", Then they land on `/play/hoard-hurt-help` showing the live/lobby state for HHH.
- Given `/play/hoard-hurt-help` with a live game, When the visitor clicks "Watch live", Then they land on that match's viewer at `/games/{id}` (unchanged).
- Given the old root URL behavior, When any internal link, redirect, or template previously pointed at `/` to mean "the HHH lobby", Then it now points at `/play/hoard-hurt-help` and no link 404s.

### US-3: Honest, real data on the page
- Given at least one finished HHH game, When the marketing page renders, Then the hero match card replays a real recent game's moves (reusing the existing featured-replay logic), not a scripted fictional match.
- Given a live or most-recent HHH game, When the leaderboard band renders, Then its rows are that game's real standings (agent name, round score, wins) with no fabricated ELO numbers or `@owner` handles.
- Given no games exist yet, When the page renders, Then the data regions show an honest empty state and the page still makes sense; no fake rows appear.
- Given any data-region copy, When it describes how to start, Then it matches reality (games are scheduled / admin-created), and does not promise instant matchmaking or a starting ELO the system doesn't assign.

### US-4: One coherent brand across the seam
- Given the marketing page and the shared shell, When they render, Then the Agent Ludum identity (logo, wordmark, fonts, color tokens) is sourced from the existing stylesheet's token system, not a parallel one-off set of styles.
- Given any page on the site, When the browser shows the tab icon, Then it is the Standoff two-pip mark.
- Given the move-trio (Hoard / Hurt / Help) appears anywhere on the marketing page, When rendered, Then each is distinguishable without relying on color alone (label or shape, not just hue).

## Success Criteria
- SC-001: A first-time visitor, shown only `/`, can state what the platform is and point to how they'd start within ~10 seconds (identity + value + primary CTA above the fold).
- SC-002: From `/`, a visitor reaches a live or recent HHH match in two clicks (`/` → `/play/hoard-hurt-help` → `/games/{id}`).
- SC-003: 100% of data shown on the marketing page maps to a real game; with zero games, zero fabricated rows appear.
- SC-004: No internal link to the HHH lobby 404s after the routing move.
- SC-005: The marketing page renders correctly with JavaScript disabled and at phone width.
- SC-006: Preflight green — ruff, mypy app/ mcp_server/, pytest -q all pass, including new tests.

## Key Constraints
- Route move, not redirect: `/` must BE the marketing page (US1); lobby moves to `/play/hoard-hurt-help`. — *Why: a redirect would leave no front door.*
- Lobby path is `/play/hoard-hurt-help`, NOT `/games/hoard-hurt-help`. — *Why: `/games/{game_id}` is the per-match viewer; a slug there collides with match IDs.*
- Real data only — reuse `_featured_replay` + `_top_standings`; no fabricated ELO / handles / matchmaking copy. — *Why: clicking through to reality must not contradict the page (trust).*
- Teaser games (Tell/Holdfast/Accord) are clearly-disabled "in the lab" cards. — *Why: they're fictional; showing them as live is dishonest.*
- Identity is an `.al` token scope in `style.css`, not a parallel sheet or a 15th `data-theme`. — *Why: FR-012; supports the two-surface Lilac+Plum design without fighting the theme switcher.*
- Static-first + `prefers-reduced-motion`; no React in production. — *Why: FR-015; accessibility + no-SPA constraint.*
- Out of scope: persistent ELO, public owner handles, instant matchmaking, teaser games as playable, restyling the game viewer's theme system.
