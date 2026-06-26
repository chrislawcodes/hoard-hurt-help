# Reuse audit — engine C-series dedup

This feature is itself a *reuse* feature: every cluster's goal is to make call sites
reuse one definition. So the audit is about WHERE each shared symbol should live and
whether an existing module already owns it. Verdicts: **reuse** (existing symbol is
the home), **extend** (existing symbol gains a param), **justified-new** (new module).

| Capability | Existing home | Verdict | Note |
|---|---|---|---|
| UTC-now (C1) | `app/engine/turn_drivers.py:50` `_now()`; also `app/aware_datetime.py` (`ensure_aware`) | reuse | Promote one `_now()` so both drivers import it. Keep it in `turn_drivers.py` or a tiny shared module; do NOT add a third now-helper. |
| Poll constant (C1) | `_SUBMIT_POLL_SECONDS` (both drivers, `0.25`) | reuse | One definition, imported by the other driver. |
| Turn-opener (C2) | `_open_turn` / `_open_actor_turn` | extend OR not-a-true-duplicate | Only unify if a 3-axis (`phase`, `resume_guard`, `set_current_round`) opener is clean; else document divergence. |
| is-bot predicate (C3) | `app/engine/user_match_start.py:44` `_is_bot(kind)` | extend/reuse | Make it the public value-level predicate (`is_bot_kind`); `turn_drivers._is_bot` (DB) and the `arena.py:297` inline call it. Stays in `user_match_start.py` (no new module — no cycle). |
| player counts (C4) | `app/engine/scheduler.py:95` `_active_player_count` | extend | Add a `reserved_aware: bool` (or two named helpers) so the watchdog (left-only) and start-floor (left+reserved) both route through it. Stays in `scheduler.py`. |
| `_has_moved` (C5) | `app/engine/connection_activity.py:80` ≈ `app/engine/agent_onboarding.py:127` | reuse | **Semantically** equivalent (same join/filter/`limit(1)`), NOT byte-identical (differ in param name + structure) — keep one, pinned by a `has_moved` equivalence test. Home must avoid a cycle between the two onboarding modules — see plan. |
| pregame-states constant (C5) | `_PREGAME_STATES` / `_UPCOMING_STATES` | reuse | One named constant `(SCHEDULED, REGISTERING)`; pick one name + cycle-free home. |
| liveness window (C6) | `app/engine/connection_health_badge.py:34` `_within_window` | reuse | Route `_connection_is_live` and `provider_loop_running` through it; relocate/rename if cross-module import needs it non-private. |
| standings sort (C7) | `app/engine/agent_play_reads.py:140` `_scoreboard_order` | reuse | `_public_standings` calls it instead of re-inlining the key. |
| cancel transition (C8) | `app/engine/match_deletion.py:33` `cancel_match` (NOT the shared target) | justified-new (tiny) | New field-only `_mark_cancelled(match, now)` in a cycle-free home (`state_machine.py`). `cancel_match` refactored to call it. |

**Duplication the plan must NOT create:** no new `utils.py`/`helpers.py`; no third now-helper; no second pregame-states constant; no count helper that merges the confirmed-vs-seated filters.

**Cycle constraints (verified):** `match_deletion.py` imports `scheduler.registry`, so the C8 helper cannot live in `match_deletion.py` if `scheduler.py`/`arena.py` must import it. `state_machine.py` is a leaf-ish home — the plan verifies `import app.engine.scheduler, app.engine.arena, app.engine.state_machine` is clean.
