# Implementation Plan: Game/Match Vocabulary Disambiguation & Full Rename

**Branch**: `feature/game-match-rename` | **Date**: 2026-06-03 | **Spec**: [spec.md](./spec.md)

## Summary

Rename the single-play concept from "Game" to "Match" across the model layer, DB schema, URLs, agent/MCP contract, and docs — while leaving the "game = title" meaning (`app/games/` module dir + registry) untouched. Lands the schema + `G_`→`M_` ID rewrite as one atomic Alembic migration (SQLite batch mode), guarded by a standalone `--dry-run` preview script with row-count verification. The public agent API and MCP tools get backward-compatible aliases so live bots survive the deploy.

## Technical Context

**Language/Version**: Python 3.12 (async FastAPI app)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x (async), Alembic, Jinja2, pydantic, the in-repo MCP server (`mcp_server/`)
**Storage**: SQLite (dev/test, in-memory for pytest) and SQLite-on-disk in prod (Railway volume). Migrations must work under SQLite → batch mode required.
**Testing**: pytest (`pytest -q`), ruff, mypy. Preflight gate = `ruff check . && mypy app/ mcp_server/ && pytest -q`.
**Target Platform**: Railway single-instance deploy; `alembic upgrade head` runs on boot.
**Performance Goals**: N/A — this is a rename; no new hot paths. Migration must complete in one boot cycle.
**Constraints**: No new suppressions; no game-rule/payoff/art changes; live bot contract must not break.
**Scale/Scope**: ~273 `game_id` references and ~27 `game_type` references across ~35 files (models, routes, schemas, engine, templates, MCP server), plus one data migration.

## Constitution Check

**Status: PASS** (validated against `CLAUDE.md` and `~/.claude/rules/data-critical-waves.md`)

- **No direct push to main / PR via feature branch**: working on `feature/game-match-rename`. ✓
- **No suppressions** (CLAUDE.md → Python Standards): the rename must not add `# type: ignore` / `# noqa`; mypy/ruff enforced by preflight. ✓
- **Async consistency / type annotations**: preserved — no signature loses annotations; renamed handlers stay `async def`. ✓
- **Test new game logic in `app/engine/`**: no game logic changes; existing engine tests must stay green, and new migration tests are added. ✓
- **Data-critical-waves rule**: REQUIRES `--dry-run`, production-shaped fixtures, real prod value/format confirmation, and a post-deploy verification checklist. All addressed below (see "Decision 2" and "Post-Deploy Verification"). ✓
- **SQLite migration batch mode** (MEMORY: sqlite-migration-batch-mode): all constraint/table/column ops wrap in `op.batch_alter_table`; guarded by `tests/test_migrations.py`. ✓
- **Spectator/leak surfaces** (MEMORY: spectator-channel-is-bot-reachable): the rename changes names, not visibility; leak tests across agent API + MCP + spectator JSON must still pass. ✓

## Architecture Decisions

### Decision 1: One atomic Alembic migration for schema rename + ID rewrite

**Chosen**: A single migration `0018_rename_game_to_match` that (a) renames the table/columns/constraints and (b) rewrites `G_`→`M_` ID values, all in one transaction.

**Rationale**:
- Atomicity (FR-008): a crash leaves the DB fully-old or fully-new, never half-renamed.
- The schema rename and the value rewrite are interdependent (the FK columns are renamed *and* their values rewritten) — splitting them into two migrations doubles the batch-recreate cost on SQLite and creates an invalid intermediate state.
- Boots once on deploy; no app code runs against a half-migrated schema.

**Alternatives considered**:
- *Two migrations (schema, then data)*: rejected — intermediate state is inconsistent and the second could fail independently.
- *Leave `G_` IDs, only rename schema*: rejected — Chris explicitly chose "rewrite all to M_".

**Tradeoffs**: Pro: atomic, simplest mental model. Con: a larger single migration; mitigated by the dry-run preview script.

### Decision 2: Standalone `--dry-run` preview script (separate from the migration)

**Chosen**: `scripts/preview_match_id_migration.py` connects read-only to a DB copy, prints the full `G_xxxx → M_xxxx` mapping and per-table affected row counts, and changes nothing. The real apply is `alembic upgrade head`.

**Rationale**: The data-critical-waves rule mandates a reviewable `--dry-run` with row counts on production-shaped fixtures *before* live execution. Alembic's own `--sql` offline mode is not enough (no row counts, no mapping). The preview script is the review artifact; its printed counts must match the post-migration counts (SC-005).

**Tradeoffs**: Pro: rule-compliant, reviewable, safe. Con: the rewrite logic exists in two places (script + migration) — mitigated by extracting the prefix-swap + count logic into one shared helper imported by both.

### Decision 3: FK rewrite via drop → update → re-add (cross-DB safe)

**Chosen**: Because `G_xxxx → M_xxxx` is a deterministic prefix swap that momentarily breaks referential integrity (updating `matches.id` orphans `players.match_id` until those rows are also updated), the migration **drops** the FK constraints (`players.match_id → matches.id`, `turns.match_id → matches.id`), runs the prefix-swap UPDATEs on all affected columns, then **re-adds** the FKs.

**Rationale**: Postgres enforces FKs at statement end (not deferred by default); SQLite batch ops recreate tables anyway. Drop/update/re-add is the one approach that is correct on both. `request_incidents.match_id` is a plain string column (not a real FK) so it only needs the value UPDATE.

**Affected columns in the value rewrite**: `matches.id`, `players.match_id`, `turns.match_id`, `request_incidents.match_id`. (Confirmed by reading the models — `strategy_prompts`, `turn_submissions`, `turn_messages`, and `bots` carry **no** game/match id; they link via `player_id`/`turn_id`.)

### Decision 4: Backward-compatible API + MCP aliases (no hard cutover)

**Chosen**: Canonical surfaces use `match_id`; legacy surfaces are kept as aliases for a deprecation window.
- **REST**: register handlers once, mount them at both `/api/matches/{match_id}/...` (canonical) and `/api/games/{game_id}/...` (legacy alias). Web pages keep `/games/<game>/matches/<match_id>` canonical with 301 redirects from `/play/<game>` and `/games/<old-id>`.
- **Request schemas**: accept both `match_id` and `game_id` (pydantic alias / validator; `game_id` populates `match_id`).
- **Response bodies**: include `match_id` AND legacy `game_id` (same value) for the window.
- **MCP tools**: keep tool *names* stable (`get_game_state`, `submit_action`, etc. — renaming them breaks clients); accept both old and new *argument* names, document new as canonical.

**Rationale**: FR-011–FR-014 + US2 — a bot mid-match must not break. Tool names are the hardest part of the public contract, so they stay; only internal semantics + arg names move.

**Tradeoffs**: Pro: zero-downtime for live bots. Con: temporary dual naming in responses/args; removal is a future task (not feature 009).

### Decision 5: Old-ID redirect map (`G_xxxx` → `M_xxxx`)

**Chosen**: Since the rewrite is a deterministic prefix swap, the redirect layer maps any incoming `G_xxxx` to `M_xxxx` by swapping the prefix (and 404s only if the resulting `M_` match truly doesn't exist). No lookup table needed.

**Rationale**: FR-010 + edge case "old URL with old ID" — bookmarks like `/games/G_0016` must not 404. A pure transform avoids storing a mapping table.

## Project Structure

Monolithic FastAPI app under `app/`, with the MCP server under `mcp_server/`. Files this feature touches:

```
app/
├── models/
│   ├── game.py                → RENAME to match.py: class Game→Match, game_type→game
│   ├── player.py              MODIFY: game_id→match_id (+ unique constraints, index names)
│   ├── turn.py                MODIFY: Turn.game_id→match_id (+ unique constraint, index)
│   ├── request_incident.py    MODIFY: game_id→match_id (plain string col + index)
│   └── __init__.py            MODIFY: export Match
├── engine/
│   ├── tokens.py              MODIFY: generate_game_id→generate_match_id, "G_"→"M_"
│   ├── scheduler.py           MODIFY: game_type/game_id refs, Game→Match
│   ├── resolver.py            MODIFY: game_id→match_id, Game→Match
│   ├── next_turn.py           MODIFY: game_id→match_id
│   ├── bot_activity.py        MODIFY: game_id→match_id
│   ├── game_insights.py       MODIFY: game_id→match_id (keep "insights" name)
│   └── sims/{service,seating,types}.py  MODIFY: game_id→match_id
├── games/                     ← UNCHANGED meaning (titles). base.py/__init__.py: game_type stays the registry key name, but the *match column* it maps to is renamed to `game`.
│   ├── base.py                MODIFY (minimal): align with match.game where it reads the column
│   └── hoard_hurt_help/game.py MODIFY: game_id→match_id, Game→Match refs
├── schemas/
│   ├── agent.py               MODIFY: match_id canonical + game_id alias (req), both in responses
│   └── spectator.py           MODIFY: same alias treatment
├── routes/
│   ├── web.py                 MODIFY: nested /games/<game>/matches/<id>; /play→/games redirect
│   ├── agent_api.py           MODIFY: /api/matches canonical + /api/games alias
│   ├── agent_next_turn.py     MODIFY: match_id; alias
│   ├── spectator_api.py       MODIFY: /api/matches + /api/games alias
│   ├── admin_api.py           MODIFY: admin match endpoints + alias
│   ├── admin_web.py           MODIFY: /admin/matches/... (+ alias or internal)
│   ├── sse.py                 MODIFY: match stream path + alias
│   └── bots_web.py            MODIFY: game_id→match_id refs
├── deps.py, broadcast.py, request_logging.py  MODIFY: game_id→match_id
├── templates/                 MODIFY: copy "game(title) vs match(play)"; *_status/detail/admin/agent_ludum.html
mcp_server/
├── server.py                  MODIFY: tool arg names accept game_id+match_id; tool names stable
└── README.md                  MODIFY: doc new arg names
migrations/versions/
└── 0018_rename_game_to_match.py   CREATE: atomic schema rename + G_→M_ rewrite (batch mode)
scripts/
└── preview_match_id_migration.py  CREATE: --dry-run mapping + row counts (read-only)
tests/
├── test_migrations.py         MODIFY/EXTEND: assert 0018 upgrades on SQLite, row counts preserved
├── test_match_rename_*.py     CREATE: model/route/alias/redirect coverage
docs/
├── DESIGN.md (root)           MODIFY: data-model + routing vocabulary
└── docs/writing-a-game-module.md  MODIFY: game=title / match=play vocabulary
```

**Structure Decision**: One app, one migration. The `app/games/` module directory and the `game_type` *registry key* keep their meaning (they identify titles); only the single-play model, its FK columns, the IDs, the URLs, and the human copy change. The match's title-slug *column* moves `game_type`→`game`, but the registry lookup key string (`"hoard-hurt-help"`) is unchanged.

## Testing Strategy

- **Migration test** (`tests/test_migrations.py`): build a SQLite DB at `0017`, seed production-shaped rows (a `G_` match with players + turns + submissions + a request_incident), run `upgrade head`, assert: table is `matches`, columns renamed, IDs are `M_`, every FK resolves, row counts unchanged. Then `downgrade` back one and assert it restores (if downgrade is supported; otherwise document irreversibility).
- **Preview-script test**: run `preview_match_id_migration.py --dry-run` against the seeded DB; assert it prints the mapping + counts and the DB is unchanged; assert its counts equal the post-migration counts (SC-005).
- **Alias tests**: hit `/api/games/{id}/...` and `/api/matches/{id}/...`, assert identical results; POST with `game_id` body and with `match_id` body, assert both work; assert responses contain both keys.
- **Redirect tests**: GET `/play/hoard-hurt-help` → 301 `/games/hoard-hurt-help`; GET `/games/G_0001` → 301 `/games/<game>/matches/M_0001`.
- **MCP tests**: call tools with old arg name and new arg name, assert both accepted.
- **Leak tests**: existing agent/MCP/spectator leak tests must still pass (names changed, visibility unchanged).
- **Preflight**: `ruff check . && mypy app/ mcp_server/ && pytest -q` green, no suppressions.

## Post-Deploy Verification (data-critical-waves rule)

Owed before marking the feature live:
- [ ] Deployed commit is on prod.
- [ ] `preview_match_id_migration.py --dry-run` was reviewed against a prod DB copy; mapping + counts sane.
- [ ] `alembic upgrade head` applied on prod; post-migration row counts for matches/players/turns/turn_submissions/request_incidents equal pre-migration counts.
- [ ] Every match PK is `M_`; zero orphaned `match_id` FKs.
- [ ] A live bot using OLD `/api/games/...` shape completes a turn; a bot using NEW `/api/matches/...` shape completes a turn.
- [ ] Spectator viewer, analysis, join, and admin pages render with new URLs; old URLs 301 correctly.
- [ ] No error spikes for 10 minutes post-deploy (watch `request_incidents`).

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Half-migrated DB on crash | Single atomic transaction (Decision 1) |
| FK violation during prefix swap | Drop → update → re-add FKs (Decision 3) |
| Live bots break on deploy | REST + MCP aliases, dual-key responses (Decision 4) |
| Old bookmarks 404 | Deterministic `G_`→`M_` redirect (Decision 5) |
| Accidentally renaming `app/games/` semantics | Explicit guardrail + grep test (FR-016) |
| `M_` rewrite collides with natively-minted `M_` | Allocator + migration agree on numbering; rewrite runs once on a DB that only has `G_` (FR-019) |
| Missed reference among 273 sites | mypy catches renamed attrs; ruff catches unused; grep audit task |

✓ Plan complete — proceeding to task breakdown (`feature-tasks`).
