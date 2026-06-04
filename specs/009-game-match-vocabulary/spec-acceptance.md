# Acceptance Criteria: Game/Match Rename

## User Stories
| ID | Title | Priority |
|----|-------|----------|
| US-1 | Spectator understands game (title) vs match (play) in UI/URLs | P1 |
| US-2 | Live bots keep playing through the rename (API/MCP aliases) | P1 |
| US-3 | Existing matches survive the G_→M_ ID migration intact | P1 |
| US-4 | Internal code consistently says "match" for a single play | P2 |
| US-5 | Docs reflect the new vocabulary | P3 |

## Acceptance Scenarios

### US-1
- Given the catalog page, When I visit `/games`, Then I see a list of games (titles) incl. Hoard Hurt Help.
- Given a game home, When I visit `/games/hoard-hurt-help`, Then I see description + lobby + a list of its matches, each labeled "match."
- Given a match, When I visit `/games/hoard-hurt-help/matches/M_0016`, Then viewer/analysis/join all say "match" and show `M_0016`.
- Given any old URL (`/play/hoard-hurt-help`, `/games/G_0016`, `/games/M_0016/analysis`), When I visit, Then I'm 301-redirected to the new canonical nested URL.

### US-2
- Given a bot calling `GET /api/games/{id}/turn` (old path), When the renamed build is live, Then it still resolves and returns a valid turn.
- Given a bot calling `POST /api/games/{id}/submit` with old payload, When it submits, Then the action is recorded.
- Given an MCP client calling `get_game_state`/`submit_action` with old arg names, When live, Then the tools accept old and new arg names.
- Given the new canonical paths `/api/matches/{match_id}/...`, When a bot uses them, Then they work identically.
- Given a response body, When a bot reads it, Then it contains new `match_id` AND legacy `game_id` (same value) for the window.

### US-3
- Given a DB with `G_` matches, When I run the migration `--dry-run`, Then it prints the rewrite plan + row counts and changes nothing.
- Given the same DB, When I run it live, Then every match PK becomes `M_xxxx` and every `match_id` FK is rewritten.
- Given the migrated DB, When I count rows in matches/players/turns/submissions/strategy_prompts, Then counts equal pre-migration counts.
- Given the migrated DB, When I open any migrated match, Then players, turn history, scores, winner, strategy prompts are all correct.
- Given SQLite + Postgres-shaped data, When constraints change, Then batch mode is used and no constraint error occurs.

### US-4
- Given the models, Then the single-play model is `Match` (PK `M_xxxx`), title referenced by `game` slug.
- Given player/turn/submission/strategy-prompt models, Then match FKs are `match_id` (where present).
- Given `app/games/`, Then it still holds game *modules* (titles) — meaning unchanged.
- Given the preflight gate, Then ruff + mypy + pytest pass with no added suppressions.

### US-5
- Given `DESIGN.md`, Then data-model + routing sections describe `Match`/`match_id`/`M_` and nested URLs.
- Given `docs/writing-a-game-module.md`, Then "game"=title and "match"=single play throughout.

## Success Criteria
- SC-001: Catalog→game→match flow uses "game" only for title, "match" only for a play; zero pages misuse "game."
- SC-002: 100% of sampled old URLs 301 to the correct new URL; no 404s.
- SC-003: An unchanged bot completes a full match across the deploy boundary using old API/MCP shapes.
- SC-004: Post-migration row counts equal pre-migration; every FK resolves (zero orphans).
- SC-005: `--dry-run` plan/counts match what is actually applied.
- SC-006: Preflight passes with no added suppressions.

## Key Constraints
- Atomic migration (schema + ID rewrite one transaction) — *Why: never leave a half-renamed DB.*
- Batch mode for all DDL — *Why: SQLite can't ALTER constraints in place; `upgrade head` dies otherwise.*
- Drop→update→re-add FKs during prefix swap — *Why: Postgres enforces FKs at statement end.*
- `--dry-run` reviewed before live apply — *Why: data-critical-waves rule.*
- API + MCP aliases, dual-key responses, stable MCP tool names — *Why: live bots must not break.*
- `app/games/` registry semantics untouched — *Why: that dir already correctly means "title."*
- Deterministic `G_`→`M_` redirect — *Why: old bookmarks must not 404.*
- No game-rule/payoff/art/move-format changes — *Why: out of scope; rename only.*
