# Implementation Quality Checklist

**Feature**: [tasks.md](../tasks.md) · validated against `CLAUDE.md`

## Code Quality (per CLAUDE.md → Python Standards)
- [ ] No `# type: ignore` / `# noqa` / swallowed exceptions added to silence the rename — Reference: CLAUDE.md § No Suppressions
- [ ] All renamed function signatures keep type annotations — Reference: CLAUDE.md § Type Annotations
- [ ] No bare `except:` introduced — Reference: CLAUDE.md § No Bare except
- [ ] DB calls / route handlers stay `async def` — Reference: CLAUDE.md § Async Consistency
- [ ] No vague filenames created; `match_id_rewrite.py` / `preview_match_id_migration.py` are domain-named — Reference: CLAUDE.md § File Structure

## Migration Quality (per ~/.claude/rules/data-critical-waves.md + MEMORY: sqlite-migration-batch-mode)
- [ ] All DDL wrapped in `op.batch_alter_table` — Reference: MEMORY sqlite-migration-batch-mode
- [ ] `--dry-run` preview script exists and is reviewed before live apply — Reference: data-critical-waves § 3
- [ ] Production-shaped fixtures used in migration tests — Reference: data-critical-waves § 4
- [ ] Real prod ID format confirmed (`G_NNNN`, zero-padded 4) before writing the swap — Reference: data-critical-waves § 2
- [ ] Migration is atomic / single transaction (FR-008)
- [ ] Shared prefix-swap helper imported by BOTH migration and preview (no drift)

## Contract Safety (per spec FR-011…016)
- [ ] Old REST paths `/api/games/...` still resolve (aliases)
- [ ] Request schemas accept `game_id` + `match_id`
- [ ] Responses carry both `match_id` and `game_id` for the window
- [ ] MCP tool NAMES unchanged; args accept old + new
- [ ] `app/games/` directory + registry key string untouched (FR-016)
- [ ] `GameState` enum name preserved (documented keeper)

## Scope Guardrails
- [ ] No changes to scoring/payoff/rules/art
- [ ] Spectator/agent/MCP visibility unchanged (names only) — Reference: MEMORY spectator-channel-is-bot-reachable
