# Tasks — Engine C-series duplication cleanup

Executable slices from `plan.md`. Each `[CHECKPOINT]` = one commit + full Preflight
Gate + (if ≥50 changed lines) a Claude regression-adversarial diff review. Anchors are
resolved by **symbol/grep**, not line number (line drift noted in reviews).

Measured baseline (Slice 0): `pytest -q` collected count on branch base = **1317**.
Final count must be ≥ baseline + new characterization tests; no test removed/skip/xfail.

## Disposition tracking (fill as slices land)

| Cluster | Disposition | Presence check | Status |
|---|---|---|---|
| C1 | unified | one `SUBMIT_POLL_SECONDS` assign + one `now_utc` def in `turn_clock.py`; no `_now`/inline `datetime.now(timezone.utc)` left in the two drivers (cancel-site `now` at C8) | ✅ Slice 1 |
| C2 | **expected: not-a-true-duplicate** | divergence documented at both openers (or single `open_turn_row` if clean) | ☐ |
| C3 | unified | one `is_bot_kind` def in `user_match_start`; `turn_drivers._is_bot` + `arena` inline delegate | ✅ Slice 2 |
| C4 | unified | one `active_player_count` def in `player_counts.py`; watchdog `exclude_reserved=False`, start-floor/arena-confirmed `True`, arena-seated `False`; watchdog inline count gone | ✅ Slice 5 |
| C5 | unified | one `has_moved` + one `PREGAME_STATES` in `onboarding_states.py`; `agent_idle._UPCOMING_STATES` replaced; mark_first_move (.limit(2), out of scope) untouched | ✅ Slice 6 |
| C6 | unified | `within_window` public; `_connection_is_live` + `provider_loop_running` delegate the window expr, keep PAUSED guard + `LOOP_RUNNING_WINDOW_SECONDS` | ✅ Slice 3 |
| C7 | unified | `_public_standings` calls `_scoreboard_order` | ✅ Slice 4 |
| C8 | unified | `rg "\.state\s*=\s*GameState\.CANCELLED" app/engine/` → only `match_cancellation.py`; all 7 inline sites call `mark_cancelled` | ☐ |

## Slices

### [CHECKPOINT] Slice 0 — measure baseline (no code)
- Run `pytest -q` (full) on branch base; record collected/passed count above. ~0 LOC.

### [CHECKPOINT] Slice 1 — C1 turn_clock (~40 LOC)
- Add `app/engine/turn_clock.py`: `SUBMIT_POLL_SECONDS = 0.25`, `def now_utc() -> datetime: return datetime.now(timezone.utc)`.
- `turn_drivers.py`: drop local `_SUBMIT_POLL_SECONDS`/`_now`; import from `turn_clock`; replace uses.
- `scheduler_turn_loop.py`: drop local `_SUBMIT_POLL_SECONDS`; replace inline `datetime.now(timezone.utc)` with `now_utc()`; replace poll constant.
- Verify: import-smoke; full Preflight.

### [CHECKPOINT] Slice 2 — C3 is_bot_kind (~25 LOC)
- `user_match_start.py`: rename `_is_bot` → `is_bot_kind(kind: object) -> bool` (keep member+value check).
- `turn_drivers._is_bot`: load Agent, `return is_bot_kind(agent.kind)`.
- `arena.py` inline: `not is_bot_kind(kind)`.
- Add unit assertion: `is_bot_kind(AgentKind.BOT) and is_bot_kind(AgentKind.BOT.value) and not is_bot_kind(<non-bot kind>)` (round-2 minor).
- Verify: import-smoke incl. the new `turn_drivers`→`user_match_start` edge; full Preflight.

### [CHECKPOINT] Slice 3 — C6 within_window (~35 LOC)
- Promote `_within_window` → `within_window` in `connection_health_badge.py`.
- `_connection_is_live`: delegate trailing window expr to `within_window`; KEEP PAUSED early-return + None guards.
- `provider_loop_running`: route per-row check through `within_window(..., LOOP_RUNNING_WINDOW_SECONDS)`; KEEP per-row None-skip loop.
- Update `connection_health.py.__all__` if it re-exports `_within_window`.
- Test (round-2 minor): naive-timestamp row exactly `LOOP_RUNNING_WINDOW_SECONDS` old at the boundary stays "running"; PAUSED connection still short-circuits.
- Verify: full Preflight.

### [CHECKPOINT] Slice 4 — C7 standings sort (~10 LOC)
- `_public_standings` calls `_scoreboard_order` instead of re-inlining the key.
- Verify: full Preflight (existing standings tests).

### [CHECKPOINT] Slice 5 — C4 player_counts (tests first) (~70 LOC)
- **First**: add `tests/test_player_counts.py` C4-watchdog test — held-seat (reserved, `left_at IS NULL`) ACTIVE game NOT cancelled by `_watchdog`; start-floor `active_player_count(exclude_reserved=True)` EXCLUDES it; show RED against a wrong (reserved-aware watchdog) merge.
- Add `app/engine/player_counts.py`: `active_player_count(db, match_id, *, exclude_reserved) -> int`.
- Rewire: `scheduler._active_player_count` body → helper(`True`); `_watchdog` inline count → helper(`False`); `arena.fill_match_with_bots` two calls (confirmed `True`, seated `False`).
- Verify: disposition grep (watchdog inline gone); full `pytest -q`.

### [CHECKPOINT] Slice 6 — C5 onboarding_states (tests first) (~60 LOC)
- **First**: `has_moved` equivalence test — defaulted-only submission → False; one real submission → True (covers both prior call sites); RED against a wrong filter.
- Add `app/engine/onboarding_states.py`: `PREGAME_STATES`, `has_moved(db, agent_id)`.
- Rewire `connection_activity._has_moved` + `agent_onboarding._has_moved` → `has_moved`; replace the 3 named constants; rename `agent_idle._UPCOMING_STATES`. Keep the two state enums/machines distinct.
- Verify: one `has_moved` def + one `PREGAME_STATES`; full `pytest -q`.

### [CHECKPOINT] Slice 7 — C8 match_cancellation (tests first) (~70 LOC)
- **First**: C8-cancel test — `mark_cancelled(match, now)` sets `state=CANCELLED` and `cancelled_at == now`; a converted caller's `cancelled_at` equals its own captured/fresh `now`; no site gains `registry.stop`; RED against a wrong (registry.stop-absorbing) merge.
- Add `app/engine/match_cancellation.py`: `mark_cancelled(match, now) -> None` (field-only).
- Rewire `cancel_match` + all **7** inline sites; each keeps its own `now` (fresh/captured), commit, logging.
- Verify: `rg "\.state\s*=\s*GameState\.CANCELLED" app/engine/` → only `match_cancellation.py`; full `pytest -q`.

### [CHECKPOINT] Slice 8 — C2 turn opener (tests first, highest risk) (~60 LOC)
- **First**: C2-seq (`_open_actor_turn` leaves `current_round` untouched, sets only `current_turn`, blind-insert) + C2-sim (`_open_turn` sets both, get-or-create resume returns same row) tests; RED against a wrong merge.
- **Disposition: expect `not-a-true-duplicate`** — document the divergence at both openers. Only build `turn_opener.open_turn_row` if it reads cleanly with no hidden 4th branch (and standardize now-source via `turn_clock.now_utc()`).
- Verify: full `pytest -q`.

### [CHECKPOINT] Slice 9 — deliver
- Final full Preflight; re-measure test count; PR with `Validation` section (ruff, mypy, full pytest, baseline→final counts) + per-cluster disposition table.

## Parallelization
Clusters share files (`scheduler.py`/`arena.py`/`turn_drivers.py`/`scheduler_turn_loop.py`
span multiple clusters), so slices run **sequentially** — no safe `[P]` parallel set.
