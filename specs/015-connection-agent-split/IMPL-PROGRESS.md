# 015 Implementation Progress (autonomous run)

Run mode: full-auto, Codex codes / Claude+Gemini review each diff / merge via /ship only if green.
Branch: `015-connection-agent-split` (worktree). Started off `origin/main` rebase.

| Slice | Status | Notes |
|---|---|---|
| 0 — models + schema | ✅ done | round-trip + import + create_all green; ruff/mypy clean |
| 1 — auth + turn resolution (HIGH-CARE) | ✅ done | 9 tests pass; ruff/mypy clean; security pass done |
| 2 — bots as agents | ⏳ next | |
| 3 — mgmt routes/templates | ⬜ | |
| 4 — runner (agentludum_connector.py) + MCP | ⬜ | |
| 5 — sweep + full preflight | ⬜ | |
| PR + /ship | ⬜ | merge only if green CI |

## Log
- Rebased onto origin/main (clean). Baseline preflight captured.

- Slice 0: Codex built models+migration. Review (Claude+Gemini): removed a redundant index, restored a wrongly-deleted Railway test. **Rejected** Gemini's false 'blocker' (downgrade game_id constraint names are intentional for the pre-0018 chain — verified by round-trip). railway test lazy-imports app.main (green at slice 5).

- Slice 1: Codex built auth+turn (require_connection, connection_activity, (agent_id,match_id) fan-out, agent_turn_token). Parallel review (Claude+Gemini+security): auth sound (unsigned token safe via Agent.connection_id ownership filter — no IDOR; freeze-class handled). Applied 2 fixes: opponent-history now uses seat_name (was leaking internal int id), next-turn LEFT-joins AgentVersion + logs a skipped version-less agent. Rejected 2 false findings. Codex over-reached into bots_setup/status/web_lobby (import-only fixes) — harmless.
