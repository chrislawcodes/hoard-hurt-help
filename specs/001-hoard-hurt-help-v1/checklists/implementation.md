# Implementation Quality Checklist

**Purpose**: validate code quality during implementation. Use as a per-phase gate.
**Feature**: [tasks.md](../tasks.md)

## Code Quality (project best practices — no constitution file present)

- [ ] Consistent Python style: black + ruff with default config, run pre-commit.
- [ ] Type annotations on every public function in `app/` and `mcp_server/`.
- [ ] Pydantic models for every HTTP request/response body; no `dict[str, Any]` at API boundaries.
- [ ] SQLAlchemy models use `Mapped[...]` type annotations (SQLAlchemy 2.x style), not legacy `Column()`.
- [ ] Async functions are actually async — no blocking sync calls inside async handlers (use `asyncio.to_thread` if absolutely needed).
- [ ] Error responses match the spec §10 envelope exactly: `{"error": {"code": ..., "message": ..., "details": ...}}`.

## Security

- [ ] Agent API keys never logged in cleartext. Argon2-hashed at rest. Shown to player exactly once.
- [ ] Session cookies set with `secure=True, httponly=True, samesite="lax"`.
- [ ] OAuth scopes limited to `openid email profile`. No additional Google APIs requested.
- [ ] `ADMIN_EMAILS` env var loaded once at startup; compared case-insensitively to Google email claim.
- [ ] Strategy prompts never exposed in turn payloads, spectator API, or any non-admin route.
- [ ] No raw SQL — all queries through SQLAlchemy ORM.
- [ ] Rate limiting on `GET /turn` (≥ 1s per agent_key) enforced server-side.

## API design

- [ ] Static prefix of `/turn` payload (`rules`, `game_id`, `your_agent_id`, `all_agent_ids`) is byte-identical across all turns of the same game. Verify by hashing in a test.
- [ ] Submit is idempotent on `(turn_token, player_id)` — duplicate calls return the first call's stored result, not a 409.
- [ ] Every endpoint listed in `contracts/api.yaml` exists and matches the documented shape.
- [ ] FastAPI tags applied per `plan-summary.md` constraint so the auto-generated OpenAPI is clean.

## URL construction

- [ ] No hardcoded URLs in templates or Python code. Use `request.url_for(...)` for internal links.
- [ ] The `BASE_URL` env var drives all externally-visible URLs (MCP setup commands, OAuth redirect, Custom GPT manifest).

## Logging

- [ ] Every accepted submission logged with `(game_id, round, turn, player_id, action, target_id, received_at)`.
- [ ] Every rejected submission logged with `(game_id, round, turn, player_id, error_code, received_at)` — but NOT the request body if it might contain sensitive content.
- [ ] Every turn resolution logged with `(game_id, round, turn, resolved_at, n_defaulted)`.
- [ ] Every state transition logged with `(game_id, from_state, to_state)`.
- [ ] Use `logging` module (not `print`); structured key=value format.

## Configuration

- [ ] All config via `app.config.Settings` (pydantic-settings). No direct `os.environ` access in business logic.
- [ ] `.env.example` lists every var read by `Settings` with a short comment.
- [ ] Database URL works with both `sqlite+aiosqlite://` and `postgresql+asyncpg://` schemes.

## Engine correctness

- [ ] Score floor at 0 applied **after** summing all deltas, not per incoming Hurt. Verified by a test that explicitly hits this case.
- [ ] Mutual-help bonus applied **before** the score floor clip.
- [ ] `points_delta` stored on `turn_submissions` is the **post-floor** actual delta, so reading the log reproduces the scoreboard.
- [ ] Round score resets to 0 at the start of every round (no carryover).
- [ ] Game tiebreaker (total in-round score across all rounds) is deterministic.

## Templates / HTMX

- [ ] All HTML escapes user-controlled fields (Jinja autoescape on).
- [ ] HTMX SSE swaps target specific element IDs; no whole-page swaps.
- [ ] HTMX endpoints that update state require CSRF protection (session-cookie + same-origin check).
