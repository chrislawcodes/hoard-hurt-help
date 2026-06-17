# Tasks — Onboarding / Auth Flow Consolidation

Derived from `plan.md`. Slices end at stable interface boundaries. Preflight Gate
(`ruff` + `mypy` + `pytest`) green at each `[CHECKPOINT]`.

> **Parallelization note (deviation from plan §Parallelization):** the plan cut
> slices 3/4 by redirect-vs-display and ran them serially because both touched
> `web_player.py`. This tasks file **re-cuts the last wave by file ownership** so
> the two halves have **disjoint write sets** and can run in parallel: **B1 owns
> `web_player.py`** (all of it) + the other redirect entry points; **B2 owns the
> display/seat sites that never touch `web_player.py`**. Same total work, now
> conflict-free. Slices 1→2 stay serial (2 depends on 1); A must land before B.

## Slice 1 — `ProviderReadiness` signal (foundation) `[CHECKPOINT]` · model: opus
**Files:** `app/engine/connection_health.py`, `tests/test_provider_readiness.py` (new)
- Add `ProviderReadiness` enum (`NO_MCP_CONNECTION`/`CONNECTED_NOT_LIVE`/`SEEN_NOT_POLLING`/`LIVE`) + `provider_readiness()` as the top-down cascade (AD-3): `loop_running → LIVE`, `has_live_current_setup → SEEN_NOT_POLLING`, `has_current_setup → CONNECTED_NOT_LIVE`, else `NO_MCP_CONNECTION`. No new SQL; calls the three existing predicates. No callers change.
- Tests: four boundaries for an **MCP** provider AND a **non-MCP** (hermes/openclaw) provider, incl. the stale-`last_seen` + fresh-`last_polled` case (cascade-order stress); PAUSED-only → `CONNECTED_NOT_LIVE`; a `before_cursor_execute` counter test asserting one call issues **≤3** queries.
- Dep: none. Est ~140 lines.

## Slice 2 — `resolve_play_setup_state` resolver + nav (ships nav ⚠) `[CHECKPOINT]` · model: opus
**Files:** `app/routes/nav_context.py`, `tests/test_play_setup_state.py` (new)
- Add `PlaySetupStage` (IntEnum) + `PlaySetupState` + `resolve_play_setup_state(db, user, *, target_match=None, target_agent=None, require=NEEDS_MCP_CONNECTION)` (AD-2/AD-4). Provider-dedup + early-exit reduction (AD-4 query bound). Reimplement `compute_nav_cta` as a thin caller.
- Ships the nav "ready" bar swap (`first_connected_at`-ever → `has_current_setup`).
- Tests: each stage transition; `require` threshold; most-ready reduction over a mixed MCP+non-MCP set; exclude `provider IS NULL`/`kind=bot`/`archived`; per-page query bound (single-provider ready user ≤ ~1 readiness query).
- Dep: Slice 1. Est ~190 lines.

## Slice 3 (B1) — `web_player.py` + redirect entry points `[CHECKPOINT]` `[P: web_player.py, auth.py, agents_create.py, web_games_catalog.py]` · model: sonnet
**Files (exclusive owner of `web_player.py`):** `app/routes/web_player.py`, `app/routes/auth.py`, `app/routes/agents_create.py`, `app/routes/web_games_catalog.py`, `tests/test_play_setup_redirects.py` (new)
- `auth.py` post-login, `agents_create` destination, `web_games_catalog` `/play`, `web_player._join_setup_redirect` → call `resolve_play_setup_state`.
- `web_player` join-form `live`/`offline`/`unconfigured` strings (`:252-273`) derive from `provider_readiness`; seat confirm/hold/connect + `seat_connect_status` poll (`:649`) read via the signal (keep the non-blocking held-seat behavior, decision 6).
- Tests: per-entry-point redirect `Location`; named `/play` lobby-drop→next-gate test; `/play ⇄ /me/connections` loop-guard (seen-but-not-polling fixture; reuse `test_smart_join_flow.py` harness); "READY user never redirected to setup" invariant.
- Dep: Slices 1+2. Disjoint from B2. Est ~190 lines.

## Slice 4 (B2) — display / seat-hold readiness sites `[CHECKPOINT]` `[P: connections_pages.py, agents_list.py, agents_detail.py, seat_hold.py]` · model: sonnet
**Files (never touches `web_player.py`):** `app/routes/connections_pages.py`, `app/routes/agents_list.py` (+ `agents_health_presenter`), `app/routes/agents_detail.py`, `app/engine/seat_hold.py`, `tests/test_readiness_adoption.py` (new)
- `connections_pages` auto-forward via `SEEN_NOT_POLLING` on **both** the page-load path (`:156-159`) and the `live_status_fragment` HTMX poll path (`:216-227`).
- `agents_list` badge + `agents_detail` readiness derive from `provider_readiness`.
- `seat_hold.confirm_seat_if_live` shares the `LIVE` boundary.
- Tests: connections-page load-path AND poll-path bar parity; before/after `agents_list` badge + `agents_detail` readiness; `confirm_seat_if_live` ↔ resolver `LIVE` parity across all four states (incl. non-MCP stale-seen-but-polling).
- Dep: Slices 1+2. Disjoint from B1. Est ~210 lines.

## Execution order
1. Slice 1 → preflight → commit.
2. Slice 2 → preflight → commit.
3. Slices 3 (B1) ‖ 4 (B2) in isolated worktrees → integrate → preflight → commit.
4. Final preflight on the integrated branch → push.
