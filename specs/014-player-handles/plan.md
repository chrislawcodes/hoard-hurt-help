# Implementation Plan: Player Handles (Public Operator Identity)

**Branch**: `claude/player-identification-strategy-fADg6` | **Date**: 2026-06-05 | **Spec**: [spec.md](./spec.md)

---

## Summary

Give each human a public **handle** (display name) on top of the existing Google
login, and credit it as `by @handle` next to their agents on the leaderboard.
Add two columns to `users` (`handle` + lowercased `handle_key`), one shared
**word filter**, a single **handle form** reached through a **gate** (you need a
handle to own an agent), and a small **read-model + template** change so the
leaderboard shows the credit. Agent display names are screened by the same word
filter. Filtering agents' per-turn **messages** is built to be reusable but
deferred to Phase 2.

Login, Google scopes, sessions, and the Elo math are all unchanged.

---

## Technical Context

**Language/Version**: Python 3.14+, async FastAPI + SQLAlchemy async; Jinja2 templates; HTMX (no SPA).
**Primary Dependencies**: none new. Reuse `require_user` (`app/deps.py`), `load_leaderboard_sections` (`app/read_models/leaderboard.py`), `validate_bot_name` (`app/routes/bots_web_support.py`).
**Storage**: SQLite (dev) / Postgres (prod) via SQLAlchemy; one new Alembic migration (`0021`). Use `op.batch_alter_table` so the column add + unique index apply on SQLite.
**Testing**: ruff + mypy + pytest (preflight). In-memory SQLite test DB.
**Target Platform**: web, desktop + phone widths.
**Constraints**: server-rendered; must work on a phone; never display email/legal name publicly; never show a human handle in the live turn feed; no suppressions; all new signatures typed.
**Scale/Scope**: 2 new small modules, 1 migration, 1 new template + 1 new route module, ~4 modified files. Phase 2 (agent-message masking) is separate.

---

## Constitution Check

**Status**: PASS

- **Async consistency** ✓ — all new routes/DB calls are `async def`.
- **No bare `except`** ✓ — validation raises typed errors; no swallowing.
- **Type annotations** ✓ — every new signature annotated; `from __future__ import annotations` where needed.
- **No suppressions** ✓ — no `# type: ignore` / `# noqa`.
- **File focus / naming** ✓ — handle logic in `app/identity/handle.py`, word list in `app/identity/word_filter.py`; no `utils.py`. App code stays in `app/`.
- **Privacy boundary** ✓ — email/real name never rendered on public surfaces (FR-009); no handle in the live viewer (FR-010).
- **SQLite migration** ✓ — `batch_alter_table` for the column add + index.
- **Tests for new logic** ✓ — handle validation, word filter, suggestion, gate, and leaderboard credit all get tests.

---

## Architecture Decisions

### Decision 1: Two columns — `handle` (display) + `handle_key` (uniqueness)

**Chosen**: Add `handle: str | None` (typed-case display) and `handle_key: str | None`
(lowercased) to `users`, plus `handle_changed_at: datetime | None`. The unique
index lives on `handle_key`.

**Rationale**: lets us show the capitals a user typed (`@ZeusMaster`) while
blocking a case-variant duplicate, and keeps uniqueness portable across SQLite
and Postgres without database-specific functional/`COLLATE NOCASE` indexes.
`NULL` keys don't collide on either engine, so all pre-handle users coexist.

**Alternatives**: single lowercased column (loses display case); functional
`lower(handle)` unique index (diverges between SQLite/Postgres).

### Decision 2: One handle form + a gate dependency, not an inline field

**Chosen**: A single form at `GET/POST /me/handle` (pick / change), pre-filled
with a suggestion. A dependency `require_user_with_handle` guards the
agent-owner surfaces: if `user.handle is None`, it redirects (303 →
`/me/handle?next=<path>`) instead of returning the page.

Gated surfaces: the bots panel (`/me/bots*`), `/play`, and the match-join route.
Pure spectator pages are **not** gated.

**Rationale**: one form serves all three entry points the spec needs — new user
before their first agent, existing agent-owner at next login, and later changes —
instead of duplicating a handle field into the new-agent form *and* building a
separate backfill path. This satisfies the spec's intent ("pick once, pre-filled,
before you own an agent") with one code path. Because a brand-new user only hits
`/me/bots` when they go to create their first agent, the redirect *is* the
"at first-agent creation" moment; existing owners hit it at next dashboard visit
(the "hard gate at next login").

**Implementation note**: a FastAPI dependency signals the redirect by raising
`HTTPException(status_code=303, headers={"Location": ...})`; browsers follow the
`Location` and ignore the JSON body. Alternative considered: an explicit check at
the top of each gated route (more repetition, same effect) — fall back to this if
the exception-redirect proves awkward in tests.

### Decision 3: Shared word filter, one module, reused everywhere

**Chosen**: `app/identity/word_filter.py` holds the slur/profanity list +
reserved words (in code, not the DB) and a `check(text) -> bool` / `mask(text)`
pair. Normalize before matching (lowercase, collapse simple look-alikes/spacing).
`app/identity/handle.py` imports it for handle rules; `validate_bot_name` imports
it for agent names.

**Rationale**: one list, three callers — adding a word covers handles, agent
names, and (Phase 2) messages at once. Honest limitation: no list is perfect, so
it is paired with admin reset (Decision 5).

**Critical**: on a rejected handle/name, never echo the offending text back — a
generic "not allowed" message only (FR-016).

### Decision 4: Leaderboard credit is display-only data on the existing read model

**Chosen**: In `load_leaderboard_sections`, add `User` to the existing
`select(Match, Player, Bot)` join and carry `owner_handle: str | None` onto the
participant → state → `LeaderboardRow`. Set it from `User.handle` for **agents
only**; `None` for Sims and for agents whose owner has no handle yet. The
template renders a muted `by @{{ row.owner_handle }}` line under the agent name
when present.

**Rationale**: the Elo math, keys, sorting, and dataclass flow are untouched —
this is one more field riding alongside `display_name`. No new endpoint, no new
query shape.

**Alternatives**: a second query keyed by bot→user (extra round trip); computing
the credit in the template from a passed dict (splits the projection).

### Decision 5: Admin reset now, reporting later

**Chosen**: Add an admin action to clear a user's handle (force-reset). No report
button in v1. A changed/reset handle's old string is freed immediately (no reuse
cooldown) — i.e. clear `handle`/`handle_key`; nothing reserves the old value.

**Rationale**: matches the locked decisions; keeps surface area small. Identity
is keyed on `users.id`, so reset/clear never touches leaderboard history.

### Decision 6: Phase 2 (agent-message masking) is separate

**Chosen**: Build `word_filter.mask()` now (replace each blocked word with
`****`, fixed length), but wire it into the turn-submission pipeline as a
follow-up. Phase 1 does **not** touch the game engine.

**Rationale**: message screening touches `app/engine` / turn submission and has
its own decision already recorded (mask, don't block, never default to Hoard).
Keeping it out of Phase 1 keeps handles shippable and the engine untouched.

---

## Project Structure

```
app/
├── identity/                      ← NEW package (domain home for identity rules)
│   ├── __init__.py
│   ├── handle.py                  ← NEW: normalize, validate, suggest-from-name
│   └── word_filter.py             ← NEW: shared slur/reserved list + check()/mask()
├── models/
│   └── user.py                    ← MODIFY: add handle, handle_key, handle_changed_at
├── deps.py                        ← MODIFY: add require_user_with_handle (gate)
├── routes/
│   ├── handle_web.py              ← NEW: GET/POST /me/handle (pick/change form)
│   ├── bots_setup.py              ← MODIFY: gate /me/bots*; (name check via support)
│   ├── bots_web_support.py        ← MODIFY: validate_bot_name calls word_filter
│   ├── web_player.py              ← MODIFY: gate the match-join route
│   ├── web_lobby.py               ← (no change: leaderboard route just passes rows)
│   └── admin_web.py               ← MODIFY: admin force-reset handle action
├── read_models/
│   └── leaderboard.py             ← MODIFY: join User; carry owner_handle
├── templates/
│   ├── handle.html                ← NEW: handle pick/change form + states
│   ├── leaderboard.html           ← MODIFY: "by @handle" line under agent name
│   └── base.html                  ← MODIFY: show "@handle · Change" in account menu
└── static/style.css               ← MODIFY: muted credit line; mobile stacking

migrations/versions/
└── 0021_add_user_handle.py        ← NEW: handle + handle_key (+unique idx) + handle_changed_at

tests/
├── test_handle.py                 ← NEW: validation, casing, suggestion fallback, uniqueness
├── test_word_filter.py            ← NEW: check() rejects, mask() → ****
├── test_handle_gate.py            ← NEW: gate redirects owner w/o handle; spectator unaffected
└── test_leaderboard.py            ← MODIFY: owner_handle present for agents, absent for Sims/no-handle
```

**Structure Decision**: ships in two independently-verifiable slices —
**Slice A** (data + handle form + gate) and **Slice B** (leaderboard credit +
name screening). Phase 2 (message masking) is a later, separate change.

---

## Build Phases

### Phase 1 — Slice A: identity, form, gate (the load-bearing half)

1. **Migration 0021** — add `handle`, `handle_key`, `handle_changed_at` to
   `users`; unique index on `handle_key`; `batch_alter_table` for SQLite.
2. **`app/identity/word_filter.py`** — reserved + slur list; `check()`, `mask()`,
   normalization. Tests.
3. **`app/identity/handle.py`** — `normalize`, `validate` (regex
   `^[A-Za-z][A-Za-z0-9_]{2,19}$`, reserved, word filter, uniqueness via
   `handle_key`), `suggest(user)` (given name → email local-part → `player<rand>`).
   Tests.
4. **`require_user_with_handle`** in `app/deps.py` — redirect to `/me/handle` when
   `handle is None`. Test (owner w/o handle redirected; spectator and
   handled-user pass).
5. **`app/routes/handle_web.py` + `templates/handle.html`** — pre-filled form,
   validation errors, 30-day change cooldown, `next` redirect after save.
6. **Apply the gate** to `/me/bots*`, `/play`, and the join route; surface
   `@handle · Change` in `base.html` account menu.

**Independent test:** a signed-in user with an agent and no handle is bounced to
a pre-filled `/me/handle`; saving a valid handle returns them to where they were;
a spectator with no agent is never gated.

### Phase 1 — Slice B: public credit + name screening

7. **`leaderboard.py`** — join `User`, carry `owner_handle`; `None` for Sims /
   no-handle. Update `test_leaderboard.py`.
8. **`leaderboard.html` + CSS** — muted `by @handle` line; phone stacking. Verify
   at phone width.
9. **`validate_bot_name`** — call `word_filter.check`; reject with generic
   message (no echo). Test.
10. **Admin force-reset** in `admin_web.py` — clears handle/handle_key; history
    intact. Test.

### Phase 2 — agent-message masking (separate, later)

11. Wire `word_filter.mask()` into turn-message submission: post the message with
    blocked words → `****`; never default the turn to Hoard. Engine tests. *(Not
    in this plan's deliverable; tracked as the follow-up.)*

---

## Preflight & Verification

Per `CLAUDE.md`, before any push/PR:

```bash
cd $(git rev-parse --show-toplevel)
python3 -m ruff check . && mypy app/ mcp_server/ && pytest -q
```

Plus manual verification with the preview harness: create a fresh user, get
gated, pick a handle, run a rated match, confirm `by @handle` shows on the
leaderboard and **not** in the live viewer; confirm phone width has no
horizontal scroll.

---

## Risks

| Risk | Mitigation |
|---|---|
| **Dependency-based redirect** (Decision 2) is an unusual FastAPI pattern. | Cover it with a gate test; fall back to an explicit per-route check if it fights the test client. |
| **Migration on SQLite** (unique index + NULLs). | `batch_alter_table`; test that multiple NULL `handle_key` rows coexist and rebuild-from-models works. |
| **Leaderboard join** accidentally drops Sim rows or duplicates. | Keep the join inner (all bots have `user_id`); set `owner_handle` only for non-Sim; assert Sim rows still appear in `test_leaderboard.py`. |
| **Word filter false sense of safety.** | Pair with admin reset; document that the list is a first line, not a guarantee. |
| **Spec said "inline field"; plan uses a gated page.** | Resolved (2026-06-05): Chris confirmed the shared `/me/handle` page + gate. Spec flow updated to match. |

---

## Open (carried from spec, non-blocking)

- Reporting UI (deferred — admin reset covers v1).
- Phase 2 message-mask on-hit details are decided (`****`); only the wiring
  remains.
